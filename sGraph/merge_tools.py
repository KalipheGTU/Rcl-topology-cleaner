
# general imports
import itertools
from qgis.core import QgsGeometry
from PyQt4.QtCore import QObject, pyqtSignal

# plugin module imports
#from utilityFunctions import *


class mergeTool(QObject):

    finished = pyqtSignal(object)
    error = pyqtSignal(Exception, basestring)
    progress = pyqtSignal(float)
    warning = pyqtSignal(str)
    killed = pyqtSignal(bool)

    def __init__(self, features, uid, errors):
        QObject.__init__(self)
        self.features = features
        self.last_fid = features[-1][0]
        self.errors = errors
        self.uid = uid

        self.vertices_occur = {}
        self.edges_occur = {}
        self.f_dict = {}
        self.self_loops = []

        for i in self.features:
            self.f_dict[i[0]] = [i[1], i[2]]
            for vertex in vertices_from_wkt_2(i[2]):
                break
            first = vertex
            for vertex in vertices_from_wkt_2(i[2]):
                pass
            last = vertex
            try:
                self.vertices_occur[first] += [i[0]]
            except KeyError, e:
                self.vertices_occur[first] = [i[0]]
            try:
                self.vertices_occur[last] += [i[0]]
            except KeyError, e:
                self.vertices_occur[last] = [i[0]]
            pair = (last, first)
            # strings are compared
            if first[0] > last[0]:
                pair = (first, last)
            try:
                self.edges_occur[pair] += [i[0]]
            except KeyError, e:
                self.edges_occur[pair] = [i[0]]

        self.con_2 = {k: v for k, v in self.vertices_occur.items() if len(v) == 2}
        self.all_con = {}
        for k, v in self.con_2.items():
            try:
                self.all_con[v[0]] += [v[1]]
            except KeyError, e:
                self.all_con[v[0]] = [v[1]]
            try:
                self.all_con[v[1]] += [v[0]]
            except KeyError, e:
                self.all_con[v[1]] = [v[0]]

        self.parallel = {k: v for k, v in self.edges_occur.items() if len(v) >= 2}
        self.duplicates = []
        for k, v in self.parallel.items():
            for x in itertools.combinations(v, 2):
                if x[0] < x[1]:
                    f_geom = QgsGeometry.fromWkt(self.f_dict[x[0]][1])
                    g_geom = QgsGeometry.fromWkt(self.f_dict[x[1]][1])
                    if f_geom.isGeosEqual(g_geom):
                        self.duplicates.append(f_geom)

        self.all_fids = [i[0] for i in self.features]
        self.fids_to_merge = list(set([fid for k, v in self.con_2.items() for fid in v]))
        self.copy_fids = list(set(self.all_fids) - set(self.fids_to_merge))
        self.feat_to_merge = [[i, self.f_dict[i][0], self.f_dict[i][1]] for i in self.fids_to_merge if i not in self.duplicates]
        self.feat_to_copy =[[i, self.f_dict[i][0], self.f_dict[i][1]] for i in self.copy_fids if i not in self.duplicates]

        self.con_1 = []
        for k,v in self.all_con.items():
            if len(v) == 1:
                self.con_1.append(k)

        self.con_1 = list(set(self.con_1))

        self.edges_to_start = [[i, self.f_dict[i][0], self.f_dict[i][1]] for i in self.con_1 ]

    def merge(self, merge_attrs=True):

        merged_features = []

        akra_count = len(self.con_1)
        f_count = 1

        edges_passed = []
        all_trees = []
        for edge in self.con_1:

            if self.killed is True:
                break

            self.progress.emit((45 * f_count / akra_count) + 45)
            f_count += 1

            if edge not in edges_passed:
                edges_passed.append(edge)
                tree = [edge]
                n_iter = 0
                x = 0
                while True:
                    last = tree[-1]
                    n_iter += 1
                    x += 1
                    # TODO in con_1 or is self loop
                    if last in self.con_1 and n_iter != 1:
                        edges_passed.append(last)
                        #print "no other line connected"
                        n_iter = 0
                        break
                    else:
                        len_tree = len(tree)
                        tree = get_next_vertex(tree, self.all_con)
                        if len(tree) == len_tree:
                            n_iter = 0
                            #print "hit end"
                            break
                    if x > 100:
                        x = 0
                        print "infinite"
                        break
                all_trees.append(tree)
                #TODO test
                if merge_attrs:
                    f_attrs_list = [self.f_dict[node][0] for node in tree]
                    f_attrs = []
                    for i in range(0, len(self.f_attrs_list[0])):
                        f_attrs += [[f_attr[i] for f_attr in f_attrs_list]]
                else:
                    f_attrs = self.f_dict[tree[0]][0]
                # new_geom = geom_dict[set_to_merge[0]]
                geom_to_merge = [QgsGeometry.fromWkt(self.f_dict[node][1]) for node in tree]
                for ind, line in enumerate(geom_to_merge[1:], start=1):
                    second_geom = line
                    first_geom = geom_to_merge[(ind - 1) % len(tree)]
                    new_geom = second_geom.combine(first_geom)
                    geom_to_merge[ind] = new_geom
                if new_geom.wkbType() == 5:
                    for linestring in new_geom.asGeometryCollection():
                        self.last_fid += 1
                        new_feat = [self.last_fid, f_attrs, linestring.exportToWkt()]
                        merged_features.append(new_feat)
                elif new_geom.wkbType() == 2:
                    self.last_fid += 1
                    new_feat = [self.last_fid, f_attrs, new_geom.exportToWkt()]
                    merged_features.append(new_feat)

        return merged_features + self.feat_to_copy




