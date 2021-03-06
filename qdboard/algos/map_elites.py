from sklearn.neighbors import KDTree
from sklearn.cluster import KMeans
from qdboard.model import *
import glob
import os
import math
import time
import numpy as np
from multiprocessing import Process, Queue
from scipy.spatial import Voronoi, voronoi_plot_2d
from qdboard.model import QDAlgorithm
import multiprocessing
import threading
import random
from pathlib import Path
import pickle
import os.path


class MapElites(QDAlgorithm):

    def __init__(self, run_id, config, b_dimensions, problem, img_visualizer=None):
        super().__init__(run_id, config, b_dimensions, problem, img_visualizer)
        self.map_elites = MapElitesRunner(run_id, config, b_dimensions, problem, img_visualizer=img_visualizer)

    def start(self):
        self.t = threading.Thread(name='child procs', target=self.map_elites.compute)
        #self.p = Process(target=self.map_elites.compute)
        self.t.stop = False
        self.t.start()
        #self.p.start()
        print("P started")

    def stop(self):
        #self.p.kill()
        self.t.stop = True
        self.t.join()

    def is_done(self):
        latest_gen = self.__latest_gen()
        if latest_gen is None:
            return False
        return latest_gen == self.config['num_gens']

    def get_archive(self):

        filename = self.map_elites.get_archive_filename()
        latest_gen = self.__latest_gen()
        if latest_gen is None:
            return {}

        filename = filename.replace("*", str(latest_gen))
        archive = pickle.load(open(f'{filename}', "rb"))

        # fit, desc, x = self.__load_data()

        filename = self.map_elites.get_centroids_filename()
        centroids = self.map_elites.load_centroids(filename)
        vor = Voronoi(centroids[:, 0:2])
        regions, vertices = self.__voronoi_finite_polygons_2d(vor)

        kdt = KDTree(centroids, leaf_size=30, metric='euclidean')

        # contours
        solutions = []
        cells = {}
        for i, region in enumerate(regions):
            polygon = vertices[region]
            cells[i] = Cell(polygon.tolist(), solutions=[])

        for key, solution in archive.items():
            img = None
            if self.img_visualizer is not None:
                img = self.img_visualizer.get_rel_path(solution)
            s = Solution(solution.solution_id, solution.genotype.tolist(), solution.behavior, solution.fitness, solution.phenotype.tolist(), img=img)
            q = kdt.query([solution.behavior], k=1)
            index = q[1][0][0]
            region = regions[index]
            # polygon = vertices[region]
            cell = cells[index]
            cell.add_solution(s)
            solutions.append(s)

        archive = Archive(self.b_dimensions, cells, solutions)

        return archive

    def __load_data(self):
        filename = self.map_elites.get_archive_filename()
        latest_gen = self.__latest_gen()
        filename = filename.replace("*", str(latest_gen))
        print("Loading ", filename)
        data = np.loadtxt(filename, dtype='str')
        fit = data[:, 0:1]
        fit = fit.astype(np.float)
        desc = data[:, 1: self.b_dims + 1]
        desc = desc.astype(np.float)
        x = data[:, self.b_dims + 1:self.b_dims + 1 + self.problem.x_dims]
        #x = x.astype(np.float)
        return fit, desc, x

    def __latest_gen(self):
        filename = self.map_elites.get_archive_filename()
        archive_files = glob.glob(filename)
        latest_gen = -1
        for file in archive_files:
           #  gen = int(file.split(".dat")[0].split("_")[-1])
            gen = int(file.split(".p")[0].split("_")[-1])
            if gen > latest_gen:
                latest_gen = gen
        if latest_gen == -1:
            return None
        return latest_gen

    def __voronoi_finite_polygons_2d(self, vor, radius=None):
        """
        Reconstruct infinite voronoi regions in a 2D diagram to finite
        regions.

        Parameters
        ----------
        vor : Voronoi
            Input diagram
        radius : float, optional
            Distance to 'points at infinity'.

        Returns
        -------
        regions : list of tuples
            Indices of vertices in each revised Voronoi regions.
        vertices : list of tuples
            Coordinates for revised Voronoi vertices. Same as coordinates
            of input vertices, with 'points at infinity' appended to the
            end.

        """

        if vor.points.shape[1] != 2:
            raise ValueError("Requires 2D input")

        new_regions = []
        new_vertices = vor.vertices.tolist()

        center = vor.points.mean(axis=0)
        if radius is None:
            radius = vor.points.ptp().max()

        # Construct a map containing all ridges for a given point
        all_ridges = {}
        for (p1, p2), (v1, v2) in zip(vor.ridge_points, vor.ridge_vertices):
            all_ridges.setdefault(p1, []).append((p2, v1, v2))
            all_ridges.setdefault(p2, []).append((p1, v1, v2))

        # Reconstruct infinite regions
        for p1, region in enumerate(vor.point_region):
            vertices = vor.regions[region]

            if all(v >= 0 for v in vertices):
                # finite region
                new_regions.append(vertices)
                continue

            # reconstruct a non-finite region
            ridges = all_ridges[p1]
            new_region = [v for v in vertices if v >= 0]

            for p2, v1, v2 in ridges:
                if v2 < 0:
                    v1, v2 = v2, v1
                if v1 >= 0:
                    # finite ridge: already in the region
                    continue

                # Compute the missing endpoint of an infinite ridge

                t = vor.points[p2] - vor.points[p1]  # tangent
                t /= np.linalg.norm(t)
                n = np.array([-t[1], t[0]])  # normal

                midpoint = vor.points[[p1, p2]].mean(axis=0)
                direction = np.sign(np.dot(midpoint - center, n)) * n
                far_point = vor.vertices[v2] + direction * radius

                new_region.append(len(new_vertices))
                new_vertices.append(far_point.tolist())

            # sort region counterclockwise
            vs = np.asarray([new_vertices[v] for v in new_region])
            c = vs.mean(axis=0)
            angles = np.arctan2(vs[:, 1] - c[1], vs[:, 0] - c[0])
            new_region = np.array(new_region)[np.argsort(angles)]

            # finish
            new_regions.append(new_region.tolist())

        return new_regions, np.asarray(new_vertices)


class MapElitesRunner:

    def __init__(self, run_id, config, b_dimensions, problem, img_visualizer=None):
        self.run_id = run_id
        self.config = config
        self.b_dimensions = b_dimensions
        self.b_dims = len(self.b_dimensions)
        self.problem = problem
        self.p = None
        self.b_mins = [dimension.min_value for dimension in self.b_dimensions]
        self.b_maxs = [dimension.max_value for dimension in self.b_dimensions]
        # init archive (empty)
        self.img_visualizer = img_visualizer
        self.archive = {}

    def __variation_continous(self, x, archive):
        y = x.copy()
        keys = list(archive.keys())
        z = archive[keys[np.random.randint(len(keys))]].genotype
        for i in range(0, len(y)):
            # iso mutation
            a = np.random.normal(0, (self.problem.x_max - self.problem.x_min) / 300.0, 1)
            y[i] = y[i] + a
            # line mutation
            b = np.random.normal(0, 20 * (self.problem.x_max - self.problem.x_min) / 300.0, 1)
            y[i] = y[i] + b * (x[i] - z[i])
        y_bounded = []
        for i in range(0, len(y)):
            elem_bounded = min(y[i], self.problem.x_max)
            elem_bounded = max(elem_bounded, self.problem.x_min)
            y_bounded.append(elem_bounded)
        return np.array(y_bounded)

    def __variation_discrete(self, x, archive):
        y = x.copy()
        keys = list(archive.keys())
        z = archive[keys[np.random.randint(len(keys))]].genotype
        # Uniform Crossover
        for i in range(self.problem.x_dims):
            if random.random() < 0.5:
                y[i] = z[i]
        # Mutation
        for i in range(self.config['discrete_muts']):
            if random.random() <= self.config['discrete_mut_prob']:
                b = random.randint(0, len(self.problem.blocks)-1)
                block = self.problem.blocks[b]
                y[i] = block
        return np.array(y)

    def __write_centroids(self, centroids):
        filename = self.get_centroids_filename()
        with open(filename, 'w') as f:
            for p in centroids:
                for item in p:
                    f.write(str(item) + ' ')
                f.write('\n')

    def __cvt(self, k, cvt_use_cache=True):
        # check if we have cached values
        if cvt_use_cache:
            fname = self.get_centroids_filename()
            if Path(fname).is_file():
                print("WARNING: using cached CVT:", fname)
                return np.loadtxt(fname)
        # otherwise, compute cvt
        x = np.random.uniform(self.b_mins, self.b_maxs, size=(self.config['cvt_samples'], self.b_dims))
        #x = np.random.uniform(self.mins, self.maxs, self.config['cvt_samples'])
        k_means = KMeans(init='k-means++', n_clusters=k,
                         n_init=1, n_jobs=-1, verbose=1, algorithm="full")
        k_means.fit(x)
        return k_means.cluster_centers_

    def __make_hashable(self, array):
        return tuple(map(float, array))

    # format: centroid fitness desc x \n
    # centroid, desc and x are vectors
    def __save_archive(self, archive, gen):
        '''
        def write_array(a, f):
            for i in a:
                f.write(str(i) + ' ')

        filename = archive_filename(self.config, self.run_id)
        filename = filename.replace("*", str(gen))
        with open(filename, 'w') as f:
            for k in archive.values():
                f.write(str(k.fitness) + ' ')
                write_array(k.behavior, f)
                write_array(k.genotype, f)
                f.write("\n")
        '''

        if self.img_visualizer is not None:
            for key, solution in archive.items():
                self.img_visualizer.save_visualization(solution)

        filename = self.get_archive_filename()
        filename = filename.replace("*", str(gen))
        pickle.dump(archive, open(f'{filename}', 'wb'))

    def __add_to_archive(self, s, kdt):
        niche_index = kdt.query([s.behavior], k=1)[1][0][0]
        niche = kdt.data[niche_index]
        n = self.__make_hashable(niche)
        elite = self.archive[n] if n in self.archive else None
        if elite is not None:
            if s.fitness > elite.fitness:
                if self.img_visualizer is not None:
                    elite_path = self.img_visualizer.get_rel_path(elite)
                    if os._exists(elite_path):
                        os.remove(elite_path)
                self.archive[n] = s
        else:
            self.archive[n] = s

    # map-elites algorithm (CVT variant)
    def compute(self):

        # Save empty archive
        self.__save_archive(self.archive, 0)

        num_cores = multiprocessing.cpu_count()
        pool = multiprocessing.Pool(num_cores)

        # create the CVT
        c = self.__cvt(self.config['num_niches'], self.config['cvt_use_cache'])
        kdt = KDTree(c, leaf_size=30, metric='euclidean')
        self.__write_centroids(c)

        init_count = 0
        t = threading.currentThread()
        # main loop
        for g in range(0, self.config['num_gens'] + 1):

            if getattr(t, "stop", True):
                return

            to_evaluate = []
            if g == 0:  # random initialization
                while init_count <= self.config['random_init']:
                    for i in range(0, self.config['random_init_batch']):
                        if self.problem.vary is not None and len(to_evaluate) == 0:
                            x = self.problem.vary
                        elif self.problem.continuous:
                            x = np.random.uniform(self.problem.x_min, self.problem.x_max, self.problem.x_dims)
                        else:
                            x = np.random.choice(self.problem.blocks, self.problem.x_dims, p=self.config['block_probs'])
                        to_evaluate += [np.array(x)]
                    if self.config['parallel']:
                        s_list = pool.map(self.problem.evaluate, to_evaluate)
                    elif self.config['batched']:
                        s_list = self.problem.evaluate_batch(to_evaluate)
                    else:
                        s_list = map(self.problem.evaluate, to_evaluate)
                    for s in s_list:
                        self.__add_to_archive(s, kdt)
                    init_count = len(self.archive)
                    to_evaluate = []
                    print("---" + str(init_count))
            else:  # variation/selection loop
                keys = list(self.archive.keys())
                for n in range(0, self.config['batch_size']):
                    # parent selection
                    x = self.archive[keys[np.random.randint(len(keys))]]
                    # copy & add variation
                    if self.problem.continuous:
                        z = self.__variation_continous(x.genotype, self.archive)
                    else:
                        z = self.__variation_discrete(x.genotype, self.archive)
                    to_evaluate += [np.array(z)]
                # parallel evaluation of the fitness
                if self.config['parallel']:
                    s_list = pool.map(self.problem.evaluate, to_evaluate)
                elif self.config['batched']:
                    s_list = self.problem.evaluate_batch(to_evaluate)
                else:
                    s_list = map(self.problem.evaluate, to_evaluate)
                # natural selection
                for s in s_list:
                    self.__add_to_archive(s, kdt)
            # write archive
            if self.config['num_gens'] == g or g == 0 or (g % self.config['dump_period'] == 0 and self.config['dump_period'] != -1):
                print("generation:", g)
                self.__save_archive(self.archive, g)

            #max_fitness = np.max([solution.fitness for key, solution in self.archive.items()])
            #print("Sum fit=", max_fitness)

    def get_centroids_filename(self):
        filename = f'centroids_{self.problem.name.strip()}_{self.problem.x_dims}_{self.problem.b_dims}_{self.config["num_niches"]}_{"-".join([dimension.name for dimension in self.b_dimensions])}.dat'
        return os.path.join(self.config['centroids_path'], filename)

    def get_archive_filename(self):
        filename = f'archive_{self.run_id}_*.p'
        return os.path.join(self.config['archive_path'], filename)

    def load_centroids(self, filename):
        points = np.loadtxt(filename)
        return points
