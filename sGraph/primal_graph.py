
# general imports
import itertools
from PyQt4.QtCore import QVariant
from qgis.core import QgsFeature, QgsGeometry, QgsField, QgsSpatialIndex, QgsVectorLayer, QgsVectorFileWriter, QgsPoint, QgsMapLayerRegistry, QgsFields
import networkx as nx
import ogr
from PyQt4.QtCore import QVariant, QObject, pyqtSignal

# plugin module imports

from generalFunctions import angle_3_points, keep_decimals
from plFunctions import pl_midpoint, point_is_vertex, find_vertex_index
from shpFunctions import edges_from_line

qgsflds_types = {u'Real': QVariant.Double , u'String': QVariant.String}

class prGraph(QObject):

    finished = pyqtSignal(object)
    error = pyqtSignal(Exception, basestring)
    progress = pyqtSignal(float)
    warning = pyqtSignal(str)

    def __init__(self, any_primal_graph, id_column, make_feat=True):
        QObject.__init__(self)
        self.obj = any_primal_graph
        self.uid = id_column
        self.n_attributes = len(self.obj.edges(data=True)[0][2].keys())
        self.uid_index = (self.obj.edges(data=True)[0][2].keys()).index(self.uid)
        self.prflds = self.obj.edges(data=True)[0][2].keys()
        if make_feat:
            features = []
            count = 1
            for edge in self.obj.edges(data=True):
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromWkt(edge[2]['Wkt']))
                feat.initAttributes(self.n_attributes)
                feat.setAttributes([edge[2][attr] for attr in self.prflds])
                feat.setFeatureId(count)
                features.append(feat)
                count += 1
            self.features = features
            self.fid_to_uid = {i.id(): i[self.uid_index] for i in self.features}
            self.uid_to_fid = {i[self.uid_index]: i.id() for i in self.features}

    # ----- ANALYSIS OPERATIONS -----

    # ----- geometry

    # dictionary uid: wkt representation
    def get_wkt_dict(self):
        return {edge[2][self.uid]: edge[2]['Wkt'] for edge in self.obj.edges(data=True)}

    # dictionary uid: qgs geometry
    def get_geom_dict(self):
        return {edge: QgsGeometry.fromWkt(wkt) for edge, wkt in self.get_wkt_dict().items()}

    # dictionary uid: geometry vertices
    def get_geom_vertices_dict(self):
        return {edge: edge_geom.asPolyline() for edge, edge_geom in self.get_geom_dict().items()}

    # dictionary uid: centroid
    # TODO: some of the centroids are not correct
    def get_centroids_dict(self):
        return {edge: pl_midpoint(edge_geom) for edge, edge_geom in self.get_geom_dict().items()}

    # ----- attributes

    # dictionary uid: attributes
    def get_attr_dict(self):
        return {edge[2][self.uid]: edge[2] for edge in self.obj.edges(data=True)}

    # ------ fields

    def get_qgs_fields(self, qgsflds):
        prflds = self.prflds
        new_fields = []

        for i in prflds:
            if i in qgsflds.keys():
                new_fields.append(QgsField(i, qgsflds[i]))
            else:
                # make field of string type
                new_fields.append(QgsField(i, QVariant.String))
        return new_fields

    # ----- GEOMETRY ITERATORS -----

    # ----- intersecting lines

    # based on bounding box
    def inter_lines_bb_iter(self):
        fid = self.fid_to_uid
        spIndex = QgsSpatialIndex()  # create spatial index object
        # insert features to index
        for f in self.features:
            spIndex.insertFeature(f)
        # find lines intersecting other linesp
        for i in self.features:
            # bbox_points = find_max_bbox(i.geometry())
            inter_lines = spIndex.intersects(i.geometry().boundingBox())
            yield fid[i.id()], [fid[line] for line in inter_lines]

    # ----- TOPOLOGY OPERATIONS -----

    # topology iterator ( point_coord : [lines] )

    def topology_iter(self, break_at_intersections):
        if break_at_intersections:
            for i, j in self.obj.adjacency_iter():
                edges = [v.values()[0][self.uid] for k, v in j.items() if len(j) == 2]
                yield i, edges
        else:
            for i, j in self.obj.adjacency_iter():
                edges = [v.values()[0][self.uid] for k, v in j.items()]
                yield i, edges

    # iterator of dual graph edges from prGraph edges

    def dl_edges_from_pr_graph(self, break_at_intersections, angular_cost = False, polylines=False):
        geometries = self.get_geom_dict()

        f_count = 1
        feat_count = self.obj.__len__()

        for i, j in self.obj.adjacency_iter():

            if break_at_intersections:
                edges = [v.values()[0][self.uid] for k, v in j.items() if len(j) == 2]
            else:
                edges = [v.values()[0][self.uid] for k, v in j.items()]

            self.progress.emit(10 * f_count / feat_count)
            f_count += 1

            for x in itertools.combinations(edges, 2):
                if angular_cost:
                    inter_point = geometries[x[0]].intersection(geometries[x[1]])
                    if polylines:
                        vertex1 = geometries[x[0]].asPolyline()[-2]
                        if inter_point.asPoint() == geometries[x[0]].asPolyline()[0]:
                            vertex1 = geometries[x[0]].asPolyline()[1]
                        vertex2 = geometries[x[1]].asPolyline()[-2]
                        if inter_point.asPoint() == geometries[x[1]].asPolyline()[0]:
                            vertex2 = geometries[x[1]].asPolyline()[1]
                    else:
                        vertex1 = geometries[x[0]].asPolyline()[0]
                        if inter_point.asPoint() == geometries[x[0]].asPolyline()[0]:
                            vertex1 = geometries[x[0]].asPolyline()[-1]
                        vertex2 = geometries[x[1]].asPolyline()[0]
                        if inter_point.asPoint() == geometries[x[1]].asPolyline()[0]:
                            vertex2 = geometries[x[1]].asPolyline()[-1]
                    angle = angle_3_points(inter_point, vertex1, vertex2)
                    yield (x[0], x[1], {'cost': angle})
                else:
                    yield (x[0], x[1], {})

    # iterator of dual graph nodes from prGraph edges

    def dl_nodes_from_pr_graph(self, dlGrpah):
        for e in self.obj.edges_iter(data=self.uid):
            if e[2] not in dlGrpah.nodes():
                yield e[2]

    def features_to_multigraph(self, fields, tolerance=None, simplify=True):
        net = nx.MultiGraph()
        for f in self.features:
            flddata = f.attributes
            g = f.geometry()
            attributes = dict(zip(fields, flddata))
            # Note:  Using layer level geometry type
            if g.wkbType() == 2:
                for edge in edges_from_line(g, attributes, tolerance, simplify):
                    e1, e2, attr = edge
                    net.add_edge(e1, e2, attr_dict=attr)
            elif g.wkbType() == 5:
                for geom_i in range(g.asGeometryCollection()):
                    for edge in edges_from_line(geom_i, attributes, tolerance, simplify):
                        e1, e2, attr = edge
                        net.add_edge(e1, e2, attr_dict=attr)
        return net

    # ----- TRANSLATION OPERATIONS -----

    def to_shp(self, path, name, crs, encoding, geom_type, qgsflds):
        if path is None:
            network = QgsVectorLayer('LineString?crs=' + crs.toWkt(), name, "memory")
        else:
            fields = QgsFields()
            for field in self.get_qgs_fields(qgsflds):
                fields.append(field)
            file_writer = QgsVectorFileWriter(path, encoding, fields, geom_type,
                                              crs, "ESRI Shapefile")
            if file_writer.hasError() != QgsVectorFileWriter.NoError:
                print "Error when creating shapefile: ", file_writer.errorMessage()
            del file_writer
            network = QgsVectorLayer(path, name, "ogr")
        # QgsMapLayerRegistry.instance().addMapLayer(network)
        pr = network.dataProvider()
        network.startEditing()
        if path is None:
            pr.addAttributes(self.get_qgs_fields(qgsflds))
        pr.addFeatures(self.features)
        network.commitChanges()
        return network

    def to_dual(self, break_at_intersections, angular_cost=True, polylines=False):
        dual_graph = nx.MultiGraph()
        # TODO: check if add_edge is quicker
        for edge in self.dl_edges_from_pr_graph(break_at_intersections, angular_cost, polylines):
            e1, e2, attr = edge
            dual_graph.add_edge(e1, e2, attr_dict=attr)
        # add nodes (some lines are not connected to others because they are pl)
        for node in self.dl_nodes_from_pr_graph(dual_graph):
            dual_graph.add_node(node)
        return dual_graph

    # ----- ALTERATION OPERATIONS -----

    def find_breakages(self, col_id):
        geometries = self.get_geom_dict()
        geom_vertices = self.get_geom_vertices_dict()

        f_count = 1
        feat_count = self.obj.size()

        for feat, inter_lines in self.inter_lines_bb_iter():

            self.progress.emit(10 * f_count / feat_count)
            f_count += 1

            f_geom = geometries[feat]
            breakages = []
            type=[]
            for line in inter_lines:
                type = []
                g_geom = geometries[line]
                intersection = f_geom.intersection(g_geom)
                # intersecting geometries at point
                if intersection.wkbType() == 1 and point_is_vertex(intersection, f_geom):
                    breakages.append(intersection)
                    type.append('inter')
                # TODO: test multipoints
                # intersecting geometries at multiple points
                elif intersection.wkbType() == 4:
                    for point in intersection.asGeometryCollection():
                        if point_is_vertex(intersection, f_geom):
                            breakages.append(point)
                    type.append('inter')
                # overalpping geometries
                elif intersection.wkbType() == 2:
                    point1 = QgsGeometry.fromPoint(QgsPoint(intersection.asPolyline()[0]))
                    point2 = QgsGeometry.fromPoint(QgsPoint(intersection.asPolyline()[-1]))
                    if point_is_vertex(point1, f_geom):
                        breakages.append(point1)
                    if point_is_vertex(point2, f_geom):
                        breakages.append(point2)
                    type.append('overlap')
                elif intersection.wkbType() == 5:
                    point1 = QgsGeometry.fromPoint(QgsPoint(intersection.asGeometryCollection()[0].asPolyline()[0]))
                    point2 = QgsGeometry.fromPoint(QgsPoint(intersection.asGeometryCollection()[-1].asPolyline()[-1]))
                    if point_is_vertex(point1, f_geom):
                        breakages.append(point1)
                    if point_is_vertex(point2, f_geom):
                        breakages.append(point2)
                    type.append('overlap')
            type = list(set(type))
            if len(breakages) > 0:
                # add first and last vertex
                vertices = set([vertex for vertex in find_vertex_index(breakages, feat, geometries)])
                vertices = list(vertices) + [0] + [len(geom_vertices[feat]) - 1]
                vertices = list(set(vertices))
                vertices.sort()
                if col_id:
                    if len(type)==2:
                        if len(vertices) != 2:
                            yield feat, vertices, ['br', 'ovrlp']
                        else:
                            yield feat, vertices, []
                    elif type == ['inter']:
                        if len(vertices) != 2:
                            yield feat, vertices, ['br']
                        else:
                            yield feat, vertices, []
                    elif type == ['overlap']:
                        if len(vertices) != 2:
                            yield feat, vertices, ['ovrlp']
                        else:
                            yield feat, vertices, []
                else:
                    yield feat, vertices,[]

    def break_graph(self, tolerance, simplify, col_id=None):
        count = 1
        geom_vertices = self.get_geom_vertices_dict()
        attr_dict = self.get_attr_dict()
        edges = {edge[2][self.uid]: (edge[0], edge[1]) for edge in self.obj.edges(data=True)}
        edges_to_remove = []
        edges_to_add = []
        breakages = []
        overlaps = []

        for k, v, error in self.find_breakages(col_id):
            attrs = attr_dict[k]
            if col_id and error == ['br', 'ovrlp']:
                breakages.append(attrs[col_id])
                overlaps.append(attrs[col_id])
            elif col_id and error == ['br']:
                breakages.append(attrs[col_id])
            elif col_id and error == ['ovrlp']:
                overlaps.append(attrs[col_id])
            count_2 = 1
            edges_to_remove.append(edges[k])
            # delete primal graph edge
            for ind, index in enumerate(v):
                if ind != len(v) - 1:
                    points = [geom_vertices[k][i] for i in range(index, v[ind + 1] + 1)]
                    attrs['broken_id'] = attrs[self.uid] + '_br_' + str(count) + '_' + str(count_2)
                    ogr_geom = ogr.Geometry(ogr.wkbLineString)
                    for i in points:
                        ogr_geom.AddPoint_2D(i[0], i[1])
                    for edge in edges_from_line(ogr_geom, attrs, tolerance, simplify):
                        e1, e2, attr = edge
                        attr['Wkt'] = ogr_geom.ExportToWkt()
                        # TODO: check why breaking a graph results in nodes
                        if e1 != e2:
                            edges_to_add.append((e1, e2, attr))
                    del ogr_geom
                    count_2 += 1
            count += 1

        self.obj.remove_edges_from(edges_to_remove)

        # update new key attribute

        for edge in self.obj.edges(data=True):
            edge[2]['broken_id'] = edge[2][self.uid]

        self.obj.add_edges_from(edges_to_add)

        return prGraph(self.obj, 'broken_id', make_feat=True), breakages, overlaps

    def find_dupl_overlaps(self):
        geometries = self.get_geom_dict()
        uid = self.uid_to_fid
        for feat, inter_lines in self.inter_lines_bb_iter():
            f_geom = geometries[feat]
            for line in inter_lines:
                g_geom = geometries[line]
                intersection = f_geom.intersection(g_geom)
                if intersection.wkbType() == 2 and g_geom.length() < f_geom.length():
                    yield line, 'del overlap'
                elif g_geom.length() == f_geom.length() and uid[line] < uid[feat]:
                    yield feat, 'del duplicate'

    # SOURCE ess toolkit
    def find_dupl_overlaps_ssx(self, orphans):
        geometries = self.get_geom_dict()
        wkt = self.get_wkt_dict()
        uid = self.uid_to_fid
        f_count = 1
        feat_count = self.obj.size()
        for feat, inter_lines in self.inter_lines_bb_iter():

            self.progress.emit(10 * f_count / feat_count)
            f_count += 1

            if orphans:
                if len(inter_lines) == 0:
                    yield line, wkt[line], 'orphans'

            f_geom = geometries[feat]
            for line in inter_lines:
                g_geom = geometries[line]
                if uid[line] < uid[feat]:
                    # duplicate geometry
                    if f_geom.isGeosEqual(g_geom):
                        yield line, wkt[line], 'duplicates'


    def rmv_dupl_overlaps(self, col_id, orphans):
        edges = {edge[2][self.uid]: (edge[0], edge[1]) for edge in self.obj.edges(data=True)}
        edges_to_remove = []
        dupl = []
        orph = []
        attr_dict = self.get_attr_dict()

        # TODO: remove edge with sepcific attributes
        for edge, geometry, error in self.find_dupl_overlaps_ssx(orphans):
            if col_id:
                if error == 'duplicates':
                    dupl.append(attr_dict[edge][col_id])
                elif error == 'orphans':
                    orph.append(attr_dict[edge][col_id])
            edges_to_remove.append(edges[edge])

        # TODO: test reconstructing the graph for speed purposes
        self.obj.remove_edges_from(edges_to_remove)

        return prGraph(self.obj, self.uid, make_feat=True), dupl, orph

    def add_edges(self, edges_to_add):
        pass

    def move_node(self, node, point_to_move_to):
        pass
