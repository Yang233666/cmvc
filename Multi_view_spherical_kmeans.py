import warnings

import numpy as np
from joblib import Parallel, delayed

# from sklearn.cluster import _k_means
from sklearn.cluster import _k_means_fast as _k_means
# from sklearn.cluster._k_means import (
from sklearn.cluster._kmeans import (
# from sklearn.cluster.k_means_ import (
    _check_sample_weight,
    _init_centroids,
    _labels_inertia,
    _tolerance,
    _validate_center_shape,
)

from sklearn.preprocessing import normalize
from sklearn.utils import check_array, check_random_state
from sklearn.utils.extmath import row_norms, squared_norm
from sklearn.utils.validation import _num_samples

from utils import cosine_distance
from cluster_f1_test import HAC_getClusters, cluster_test, embed2f1
from warnings import simplefilter
simplefilter(action='ignore', category=FutureWarning)


def multi_view_centers_dense(X_view_1, X_view_2, sample_weight, labels_view_1, labels_view_2, n_clusters, distances):
    """M step of the K-means EM algorithm

    Computation of cluster centers / means.

    Parameters
    ----------
    X_view_1 : array-like, shape (n_samples, n_features)

    X_view_2 : array-like, shape (n_samples, n_features)

    sample_weight : array-like, shape (n_samples,)
        The weights for each observation in X.

    labels_view_1 : array of integers, shape (n_samples)
        Current label assignment

    labels_view_2 : array of integers, shape (n_samples)
        Current label assignment

    n_clusters : int
        Number of desired clusters

    distances : array-like, shape (n_samples)
        Distance to closest cluster for each sample.

    Returns
    -------
    centers : array, shape (n_clusters, n_features)
        The resulting centers
    """
    n_samples = int(X_view_1.shape[0])  # 23735
    view_1_n_features = int(X_view_1.shape[1])  # 300
    view_2_n_features = int(X_view_2.shape[1])  # 300

    dtype = np.float32
    centers_view_1 = np.zeros((n_clusters, view_1_n_features), dtype=dtype)  # <class 'numpy.ndarray'> (9000, 300)  fenzi
    centers_view_2 = np.zeros((n_clusters, view_2_n_features), dtype=dtype)  # <class 'numpy.ndarray'> (9000, 300)  fenzi
    weight_in_cluster = np.zeros((n_clusters,), dtype=dtype)  # <class 'numpy.ndarray'> (9000,)  fenmu

    # sample_weight: <class 'numpy.ndarray'> (23735,) [1. 1. 1. ... 1. 1. 1.]
    # labels_view_1 <class 'numpy.ndarray'> (23735,) [5089 3052 4057 ... 8761 2708  332]

    for i in range(n_samples):
        c1 = labels_view_1[i]
        c2 = labels_view_2[i]
        if c1 == c2:  # only those examples are included that both views agree on
            weight_in_cluster[c1] += sample_weight[i]
    empty_clusters = np.where(weight_in_cluster == 0)[0]
    # weight_in_cluster: <class 'numpy.ndarray'> (9000,) [2. 4. 4. ... 3. 1. 1.]
    # print('empty_clusters:', type(empty_clusters), empty_clusters.shape, empty_clusters)
    # empty_clusters: <class 'numpy.ndarray'> (7,) [1347 2139 2382 5414 6348 6406 7387]
    # maybe also relocate small clusters?

    if len(empty_clusters):
        # find points to reassign empty clusters to
        far_from_centers = distances.argsort()[::-1]
        # far_from_centers: <class 'numpy.ndarray'> (23735,) [ 7254 10213 15661 ... 15342 13377 19714]

        for i, cluster_id in enumerate(empty_clusters):
            # XXX two relocated clusters could be close to each other
            far_index = far_from_centers[i]
            centers_view_1[cluster_id] = X_view_1[far_index] * sample_weight[far_index]
            centers_view_2[cluster_id] = X_view_2[far_index] * sample_weight[far_index]
            weight_in_cluster[cluster_id] = sample_weight[far_index]

    for i in range(n_samples):
        for j in range(view_1_n_features):
            centers_view_1[labels_view_1[i], j] += X_view_1[i, j] * sample_weight[i]
        for j in range(view_2_n_features):
            centers_view_2[labels_view_2[i], j] += X_view_2[i, j] * sample_weight[i]
    centers_view_1 /= weight_in_cluster[:, np.newaxis]
    centers_view_2 /= weight_in_cluster[:, np.newaxis]
    # weight_in_cluster[:, np.newaxis]: <class 'numpy.ndarray'> (9000, 1)
    return centers_view_1, centers_view_2


def _check_normalize_sample_weight(sample_weight, X):
    """Set sample_weight if None, and check for correct dtype"""

    sample_weight_was_none = sample_weight is None

    sample_weight = _check_sample_weight(sample_weight, X, dtype=X.dtype)
    if not sample_weight_was_none:
        # normalize the weights to sum up to n_samples
        # an array of 1 (i.e. samples_weight is None) is already normalized
        n_samples = len(sample_weight)
        scale = n_samples / sample_weight.sum()
        sample_weight *= scale
    return sample_weight


def multi_view_labels_inertia_precompute_dense(X_view_1, X_view_2, centers_view_1, centers_view_2):
    """Compute labels and inertia using a full distance matrix.

    This will overwrite the 'distances' array in-place.

    Parameters
    ----------
    X_view_1 : numpy array, shape (n_sample, n_features)
        Input data in view 1.

    X_view_2 : numpy array, shape (n_sample, n_features)
        Input data in view 2.

    centers_view_1 : numpy array, shape (n_clusters, n_features)
        Cluster centers which data is assigned to in view 1.

    centers_view_2 : numpy array, shape (n_clusters, n_features)
        Cluster centers which data is assigned to in view 2.

    Returns
    -------
    labels : numpy array, dtype=np.int, shape (n_samples,)
        Indices of clusters that samples are assigned to.

    """
    n_samples = X_view_1.shape[0]
    n_clusters = centers_view_1.shape[0]
    labels = np.full(n_samples, -1, np.int32)
    inertia = 0

    for i in range(n_samples):
        x_view_1 = X_view_1[i]
        x_view_2 = X_view_2[i]
        dis_min = 10
        dis_index = 0
        for j in range(n_clusters):
            m_j_view_1 = centers_view_1[j]
            m_j_view_2 = centers_view_2[j]
            view_1_cos_dis = cosine_distance(x_view_1, m_j_view_1)
            view_2_cos_dis = cosine_distance(x_view_2, m_j_view_2)
            dis_j = view_1_cos_dis + view_2_cos_dis
            if dis_j < dis_min:
                dis_min = dis_j
                dis_index = j
                # print('dis_min:', dis_min, 'dis_index:', dis_index)
        labels[i] = dis_index
        inertia += dis_min
    # cython k-means code assumes int32 inputs
    labels = labels.astype(np.int32, copy=False)
    # print('labels:', type(labels), labels.shape, labels)
    inertia = float(inertia)
    # print('inertia:', type(inertia), inertia)
    return labels, inertia


def multi_view_labels_inertia(X_view_1, X_view_2, sample_weight, x_view_1_squared_norms, x_view_2_squared_norms,
                              centers_view_1, centers_view_2, precompute_distances=True, distances=None):
    """E step of the K-means EM algorithm.

    Compute the labels and the inertia of the given samples and centers.
    This will compute the distances in-place.

    Parameters
    ----------
    X_view_1 : float64 array-like or CSR sparse matrix, shape (n_samples, n_features)
        The input samples to assign to the labels in view 1.

    X_view_2 : float64 array-like or CSR sparse matrix, shape (n_samples, n_features)
        The input samples to assign to the labels in view 2.

    sample_weight : array-like, shape (n_samples,)
        The weights for each observation in X.

    x_view_1_squared_norms : array, shape (n_samples,)
        Precomputed squared euclidean norm of each data point in view 1, to speed up
        computations.

    x_view_2_squared_norms : array, shape (n_samples,)
        Precomputed squared euclidean norm of each data point in view 2, to speed up
        computations.

    centers_view_1 : float array, shape (k, n_features)
        The cluster centers in view 1.

    centers_view_2 : float array, shape (k, n_features)
        The cluster centers in view 2.

    precompute_distances : boolean, default: True
        Precompute distances (faster but takes more memory).

    distances : float array, shape (n_samples,)
        Pre-allocated array to be filled in with each sample's distance
        to the closest center.

    Returns
    -------
    labels : int array of shape(n)
        The resulting assignment
    """
    n_samples = X_view_1.shape[0]
    sample_weight = _check_normalize_sample_weight(sample_weight, X_view_1)
    # set the default value of centers to -1 to be able to detect any anomaly
    # easily
    labels = np.full(n_samples, -1, np.int32)
    if distances is None:
        distances = np.zeros(shape=(0,), dtype=X_view_1.dtype)
    # distances will be changed in-place
    if precompute_distances:
        return multi_view_labels_inertia_precompute_dense(X_view_1, X_view_2, centers_view_1, centers_view_2)
    inertia_view_1 = _k_means._assign_labels_array(
        X_view_1, sample_weight, x_view_1_squared_norms, centers_view_1, labels,
        distances=distances)
    inertia_view_2 = _k_means._assign_labels_array(
        X_view_2, sample_weight, x_view_2_squared_norms, centers_view_2, labels,
        distances=distances)
    inertia = inertia_view_1 + inertia_view_2
    return labels, inertia


def multi_view_spherical_kmeans_single_lloyd(
    X_view_1,
    X_view_2,
    n_clusters,
    sample_weight=None,
    max_iter=300,
    init="k-means++",
    verbose=False,
    x_view_1_squared_norms=None,
    x_view_2_squared_norms=None,
    random_state=None,
    tol=1e-4,
    precompute_distances=True,
    p=None,
    side_info=None,
    true_ent2clust=None,
    true_clust2ent=None
):
    """
    Modified from sklearn.cluster.k_means_.k_means_single_lloyd.
    """
    random_state = check_random_state(random_state)
    sample_weight = _check_sample_weight(sample_weight, X_view_2)

    best_labels, best_inertia, best_centers = None, None, None

    step_list, view_1_loss, view_2_loss, mvc_loss, view_1_f1, view_2_f1, mvc_f1 = [], [], [], [], [], [], []

    # init
    centers_view_2 = _init_centroids(
        X_view_2, n_clusters, init, random_state=random_state, x_squared_norms=x_view_2_squared_norms
    )
    # print('init centers_view_2:', type(centers_view_2), centers_view_2.shape)

    if verbose:
        print("Initialization complete")

    # Allocate memory to store the distances for each sample to its
    # closer center for reallocation in case of ties
    distances = np.zeros(shape=(X_view_2.shape[0],), dtype=X_view_2.dtype)

    x_list = [X_view_1, X_view_2]
    x_squared_norms_list = [x_view_1_squared_norms, x_view_2_squared_norms]

    # E step view 2: labels assignment
    if p.step_0_use_hac:
        cluster_threshold_real = 0.33
        labels, clusters_center = HAC_getClusters(p, X_view_2, cluster_threshold_real)
        # print('labels:', type(labels), len(labels), labels)
        # labels_2 = list(set(labels))
        # print('labels_2:', len(labels_2), labels_2)
        cluster_predict_list = list(labels)
        print('E step view 2 use HAC:')
        cluster_test(p, side_info, cluster_predict_list, true_ent2clust, true_clust2ent, True)

    else:
        # labels, inertia = _labels_inertia(
        #     X_view_2,
        #     sample_weight,
        #     x_view_2_squared_norms,
        #     centers_view_2,
        #     precompute_distances=precompute_distances,
        #     distances=distances,
        # )
        labels, inertia = _labels_inertia(
            X_view_2,
            sample_weight,
            x_view_2_squared_norms,
            centers_view_2,
        )

        # print('labels:', type(labels), len(labels), labels)
        # labels_2 = list(set(labels))
        # print('labels_2:', len(labels_2), labels_2)
        if verbose:
            cluster_predict_list = list(labels)
            print('E step view 2 use k-means:')
            ave_prec, ave_recall, ave_f1, macro_prec, micro_prec, pair_prec, macro_recall, micro_recall, pair_recall, \
            macro_f1, micro_f1, pair_f1, model_clusters, model_Singletons, gold_clusters, gold_Singletons =\
                cluster_test(p, side_info, cluster_predict_list, true_ent2clust, true_clust2ent, True)
    # labels: <class 'numpy.ndarray'> (23735,) [3016 2752 3836 ... 2372 2267 8109]
    # inertia: <class 'numpy.float32'> 4191.952

    labels_view_1 = np.zeros_like(labels)
    labels_view_2 = np.zeros_like(labels)

    # iterations
    inertia_totol = 0
    for i in range(max_iter):
        inertia_totol = 0
        step_list.append(i)
        for j in range(len(x_list)):
            x = x_list[j]
            x_squared_norms = x_squared_norms_list[j]
            labels_old = labels.copy()

            # M step: computation of the means
            centers = _k_means._centers_dense(
                x.astype(np.float),
                sample_weight.astype(np.float),
                labels_old,
                n_clusters,
                distances.astype(np.float),
            )

            # l2-normalize centers (this is the main contribution here)
            centers = normalize(centers)

            # E step: labels assignment
            # TODO: _labels_inertia should be done with cosine distance
            #       since ||a - b|| = 2(1 - cos(a,b)) when a,b are unit normalized
            #       this doesn't really matter.
            # labels, inertia = _labels_inertia(
            #     x,
            #     sample_weight,
            #     x_squared_norms,
            #     centers,
            #     precompute_distances=precompute_distances,
            #     distances=distances,
            # )
            labels, inertia = _labels_inertia(
                x,
                sample_weight,
                x_squared_norms,
                centers,
            )
            inertia_totol += inertia

            if j == 0:
                if verbose:
                    print("view 1 Iteration %2d, inertia %.3f" % (i, inertia))
                    print('centers:', type(centers), centers.shape)
                    view_1_inertia = inertia
                    view_1_loss.append(view_1_inertia)
                labels_view_1 = labels.copy()
                if verbose:
                    if i % 1 == 0:
                        ave_prec, ave_recall, ave_f1, macro_prec, micro_prec, pair_prec, macro_recall, micro_recall, pair_recall, \
                        macro_f1, micro_f1, pair_f1, model_clusters, model_Singletons, gold_clusters, gold_Singletons \
                            = cluster_test(p, side_info, labels_view_1, true_ent2clust, true_clust2ent)
                        print('Ave-F1=', ave_f1, 'macro_f1=', macro_f1, 'micro_f1=', micro_f1, 'pair_f1=', pair_f1)
                        print('Model: #Clusters: %d, #Singletons %d' % (model_clusters, model_Singletons))
                        print('Gold: #Clusters: %d, #Singletons %d' % (gold_clusters, gold_Singletons))
                        print()
                        view_1_f1.append(ave_f1)
            if j == 1:
                if verbose:
                    print("view 2 Iteration %2d, inertia %.3f" % (i, inertia))
                    print('centers:', type(centers), centers.shape)
                    view_2_inertia = inertia
                    view_2_loss.append(view_2_inertia)
                labels_view_2 = labels.copy()
                if verbose:
                    if i % 1 == 0:
                        ave_prec, ave_recall, ave_f1, macro_prec, micro_prec, pair_prec, macro_recall, micro_recall, pair_recall, \
                        macro_f1, micro_f1, pair_f1, model_clusters, model_Singletons, gold_clusters, gold_Singletons \
                            = cluster_test(p, side_info, labels_view_2, true_ent2clust, true_clust2ent)
                        print('Ave-F1=', ave_f1, 'macro_f1=', macro_f1, 'micro_f1=', micro_f1, 'pair_f1=', pair_f1)
                        print('Model: #Clusters: %d, #Singletons %d' % (model_clusters, model_Singletons))
                        print('Gold: #Clusters: %d, #Singletons %d' % (gold_clusters, gold_Singletons))
                        print()
                        view_2_f1.append(ave_f1)

        if verbose:
            print("Iteration %2d, inertia_totol %.3f" % (i, inertia_totol))

            # M step: computation of the means
            best_centers_view_1, best_centers_view_2 = multi_view_centers_dense(
                X_view_1.astype(np.float),
                X_view_2.astype(np.float),
                sample_weight.astype(np.float),
                labels_view_1,
                labels_view_2,
                n_clusters,
                distances.astype(np.float),
            )

            # l2-normalize centers (this is the main contribution here)
            best_centers_view_1 = normalize(best_centers_view_1)
            best_centers_view_2 = normalize(best_centers_view_2)

            # print('best_centers_view_1:', type(best_centers_view_1), best_centers_view_1.shape, 'best_centers_view_2:', type(best_centers_view_2), best_centers_view_2.shape)
            # E step: labels assignment
            # TODO: _labels_inertia should be done with cosine distance
            #       since ||a - b|| = 2(1 - cos(a,b)) when a,b are unit normalized
            #       this doesn't really matter.
            best_labels, best_inertia = multi_view_labels_inertia(
                X_view_1,
                X_view_2,
                sample_weight,
                x_view_1_squared_norms,
                x_view_2_squared_norms,
                best_centers_view_1,
                best_centers_view_2,
                precompute_distances=precompute_distances,
                distances=distances,
            )

            print('Best labels, Best_inertia: ', best_inertia)
            ave_prec, ave_recall, ave_f1, macro_prec, micro_prec, pair_prec, macro_recall, micro_recall, pair_recall, \
            macro_f1, micro_f1, pair_f1, model_clusters, model_Singletons, gold_clusters, gold_Singletons = \
                cluster_test(p, side_info, best_labels, true_ent2clust, true_clust2ent, True)
            mvc_loss.append(best_inertia)
            mvc_f1.append(ave_f1)
            print()

        if inertia_totol <= tol:
            print("Converged at iteration %d: " "intertia tolol %e within tolerance %e" % (i, inertia_totol, tol))
            # if verbose:
            #     print(
            #         "Converged at iteration %d: "
            #         "intertia tolol %e within tolerance %e" % (i, inertia_totol, tol)
            #     )
            break

        # exit()

    save_path = '../file/OPIEC59k_test/multi_view/figure'
    import os
    if not os.path.exists(save_path):
        os.makedirs(save_path)
    np.save(os.path.join(save_path, 'step_list'), step_list)
    np.save(os.path.join(save_path, 'view_1_loss'), view_1_loss)
    np.save(os.path.join(save_path, 'view_2_loss'), view_2_loss)
    np.save(os.path.join(save_path, 'mvc_loss'), mvc_loss)
    np.save(os.path.join(save_path, 'view_1_f1'), view_1_f1)
    np.save(os.path.join(save_path, 'view_2_f1'), view_2_f1)
    np.save(os.path.join(save_path, 'mvc_f1'), mvc_f1)
    print('step_list:', type(step_list), len(step_list))
    print('view_1_loss:', type(view_1_loss), len(view_1_loss))
    print('view_2_loss:', type(view_2_loss), len(view_2_loss))
    print('mvc_loss:', type(mvc_loss), len(mvc_loss))
    print('view_1_f1:', type(view_1_f1), len(view_1_f1))
    print('view_2_f1:', type(view_2_f1), len(view_2_f1))
    print('mvc_f1:', type(mvc_f1), len(mvc_f1))
    print('Successfully saved step_list and loss_list')
    print()

    # 导入matplotlib的pyplot模块
    import matplotlib.pyplot as plt

    l1 = plt.plot(step_list, view_1_loss, 'r--', label='Loss of fact view')
    l2 = plt.plot(step_list, view_2_loss, 'g--', label='Loss of context view')
    l3 = plt.plot(step_list, mvc_loss, 'b--', label='Loss of CMVC')
    plt.plot(step_list, view_1_loss, 'ro-', step_list, view_1_loss, 'g+-', step_list, view_1_loss, 'b^-')
    plt.title('step_loss')
    plt.xlabel('iteration')
    plt.ylabel('loss')
    plt.legend()
    fig_file = save_path + '/step_loss.jpg'
    plt.savefig(fig_file)

    l1 = plt.plot(step_list, view_1_f1, 'r--', label='Average F1 of fact view')
    l2 = plt.plot(step_list, view_2_f1, 'g--', label='Average F1 of context view')
    l3 = plt.plot(step_list, mvc_f1, 'b--', label='Average F1 of CMVC')
    plt.plot(step_list, view_1_f1, 'ro-', step_list, view_1_f1, 'g+-', step_list, view_1_f1, 'b^-')
    plt.title('Step_Average F1')
    plt.xlabel('iteration')
    plt.ylabel('Average F1')
    plt.legend()
    fig_file = save_path + '/step_f1.jpg'
    plt.savefig(fig_file)

    # plt.figure(1)
    # plt.plot(step_list, loss_list)
    # plt.xlabel('step')  # 添加文本 #x轴文本
    # plt.ylabel('loss')  # y轴文本
    # plt.title('step_loss')  # 标题
    # fig_file = save_path + '/step_loss.jpg'
    # plt.savefig(fig_file)

    # plt.figure(2)
    # plt.plot([0, 1, 2, 3], ave_f1_list)
    # plt.xlabel('step')  # 添加文本 #x轴文本
    # plt.ylabel('ave_f1')  # y轴文本
    # plt.title('step_ave_f1')  # 标题
    # fig_file = save_path + '/step_ave_f1.jpg'
    # plt.savefig(fig_file)
    exit()

    if inertia_totol > 0:
        # rerun E-step in case of non-convergence so that predicted labels
        # match cluster centers

        # M step: computation of the means
        best_centers_view_1, best_centers_view_2 = multi_view_centers_dense(
            X_view_1.astype(np.float),
            X_view_2.astype(np.float),
            sample_weight.astype(np.float),
            labels_view_1,
            labels_view_2,
            n_clusters,
            distances.astype(np.float),
        )

        # l2-normalize centers (this is the main contribution here)
        best_centers_view_1 = normalize(best_centers_view_1)
        best_centers_view_2 = normalize(best_centers_view_2)

        # print('best_centers_view_1:', type(best_centers_view_1), best_centers_view_1.shape, 'best_centers_view_2:', type(best_centers_view_2), best_centers_view_2.shape)
        # E step: labels assignment
        # TODO: _labels_inertia should be done with cosine distance
        #       since ||a - b|| = 2(1 - cos(a,b)) when a,b are unit normalized
        #       this doesn't really matter.
        best_labels, best_inertia = multi_view_labels_inertia(
            X_view_1,
            X_view_2,
            sample_weight,
            x_view_1_squared_norms,
            x_view_2_squared_norms,
            best_centers_view_1,
            best_centers_view_2,
            precompute_distances=precompute_distances,
            distances=distances,
        )

        # print('Best labels, Best_inertia: ', best_inertia)
        # cluster_test(p, side_info, best_labels, true_ent2clust, true_clust2ent, True)
        # print()

    return best_labels, best_inertia, i + 1

def multi_view_spherical_k_means(
    X_view_1,
    X_view_2,
    n_clusters,
    sample_weight=None,
    init="k-means++",
    n_init=10,
    max_iter=300,
    verbose=False,
    tol=1e-4,
    random_state=None,
    copy_x=True,
    n_jobs=1,
    algorithm="auto",
    return_n_iter=False,
    p=None,
    side_info=None,
    true_ent2clust=None,
    true_clust2ent=None
):
    """Modified from sklearn.cluster.k_means_.k_means.
    """
    if n_init <= 0:
        raise ValueError(
            "Invalid number of initializations."
            " n_init=%d must be bigger than zero." % n_init
        )
    random_state = check_random_state(random_state)

    if max_iter <= 0:
        raise ValueError(
            "Number of iterations should be a positive number,"
            " got %d instead" % max_iter
        )

    best_inertia = np.infty
    # avoid forcing order when copy_x=False
    order = "C" if copy_x else None
    X_view_1 = check_array(
        X_view_1, accept_sparse=False, dtype=[np.float64, np.float32], order=order, copy=copy_x
    )
    X_view_2 = check_array(
        X_view_2, accept_sparse=False, dtype=[np.float64, np.float32], order=order, copy=copy_x
    )
    # verify that the number of samples given is larger than k
    if _num_samples(X_view_1) < n_clusters:
        raise ValueError(
            "X_view_1 's n_samples=%d should be >= n_clusters=%d" % (_num_samples(X_view_1), n_clusters)
        )
    if _num_samples(X_view_2) < n_clusters:
        raise ValueError(
            "X_view_2 's n_samples=%d should be >= n_clusters=%d" % (_num_samples(X_view_2), n_clusters)
        )
    tol_view_1 = _tolerance(X_view_1, tol)
    tol_view_2 = _tolerance(X_view_2, tol)
    tol = (tol_view_1 + tol_view_2) / 2

    if hasattr(init, "__array__"):
        init = check_array(init, dtype=X_view_1.dtype.type, order="C", copy=True)
        _validate_center_shape(X_view_1, n_clusters, init)
        init = check_array(init, dtype=X_view_2.dtype.type, order="C", copy=True)
        _validate_center_shape(X_view_2, n_clusters, init)

        if n_init != 1:
            warnings.warn(
                "Explicit initial center position passed: "
                "performing only one init in k-means instead of n_init=%d" % n_init,
                RuntimeWarning,
                stacklevel=2,
            )
            n_init = 1

    # precompute squared norms of data points
    x_view_1_squared_norms = row_norms(X_view_1, squared=True)
    x_view_2_squared_norms = row_norms(X_view_2, squared=True)

    if n_jobs == 1:
        # For a single thread, less memory is needed if we just store one set
        # of the best results (as opposed to one set per run per thread).
        for it in range(n_init):
            # run a k-means once
            labels, inertia, n_iter_ = multi_view_spherical_kmeans_single_lloyd(
                X_view_1,
                X_view_2,
                n_clusters,
                sample_weight,
                max_iter=max_iter,
                init=init,
                verbose=verbose,
                tol=tol,
                x_view_1_squared_norms=x_view_1_squared_norms,
                x_view_2_squared_norms=x_view_2_squared_norms,
                random_state=random_state,
                p=p,
                side_info=side_info,
                true_ent2clust=true_ent2clust,
                true_clust2ent=true_clust2ent
            )

            # determine if these results are the best so far
            if best_inertia is None or inertia < best_inertia:
                best_labels = labels.copy()
                best_inertia = inertia
                best_n_iter = n_iter_
    else:
        # parallelisation of k-means runs
        seeds = random_state.randint(np.iinfo(np.int32).max, size=n_init)
        results = Parallel(n_jobs=n_jobs, verbose=0)(
            delayed(multi_view_spherical_kmeans_single_lloyd)(
                X_view_1,
                X_view_2,
                n_clusters,
                sample_weight,
                max_iter=max_iter,
                init=init,
                verbose=verbose,
                tol=tol,
                x_view_1_squared_norms=x_view_1_squared_norms,
                x_view_2_squared_norms=x_view_2_squared_norms,
                # Change seed to ensure variety
                random_state=seed,
                p=p,
                side_info=side_info,
                true_ent2clust=true_ent2clust,
                true_clust2ent=true_clust2ent
            )
            for seed in seeds
        )

        # Get results with the lowest inertia
        labels, inertia, n_iters = zip(*results)
        best = np.argmin(inertia)
        best_labels = labels[best]
        best_inertia = inertia[best]
        best_n_iter = n_iters[best]

    if return_n_iter:
        return best_labels, best_inertia, best_n_iter
    else:
        return best_labels, best_inertia


class Multi_view_SphericalKMeans(object):
    """Spherical K-Means clustering

    Modfication of sklearn.cluster.KMeans where cluster centers are normalized
    (projected onto the sphere) in each iteration.

    Parameters
    ----------

    n_clusters : int, optional, default: 8
        The number of clusters to form as well as the number of
        centroids to generate.

    max_iter : int, default: 300
        Maximum number of iterations of the k-means algorithm for a
        single run.

    n_init : int, default: 10
        Number of time the k-means algorithm will be run with different
        centroid seeds. The final results will be the best output of
        n_init consecutive runs in terms of inertia.

    init : {'k-means++', 'random' or an ndarray}
        Method for initialization, defaults to 'k-means++':
        'k-means++' : selects initial cluster centers for k-mean
        clustering in a smart way to speed up convergence. See section
        Notes in k_init for more details.
        'random': choose k observations (rows) at random from data for
        the initial centroids.
        If an ndarray is passed, it should be of shape (n_clusters, n_features)
        and gives the initial centers.

    tol : float, default: 1e-4
        Relative tolerance with regards to inertia to declare convergence

    n_jobs : int
        The number of jobs to use for the computation. This works by computing
        each of the n_init runs in parallel.
        If -1 all CPUs are used. If 1 is given, no parallel computing code is
        used at all, which is useful for debugging. For n_jobs below -1,
        (n_cpus + 1 + n_jobs) are used. Thus for n_jobs = -2, all CPUs but one
        are used.

    random_state : integer or numpy.RandomState, optional
        The generator used to initialize the centers. If an integer is
        given, it fixes the seed. Defaults to the global numpy random
        number generator.

    verbose : int, default 0
        Verbosity mode.

    copy_x : boolean, default True
        When pre-computing distances it is more numerically accurate to center
        the data first.  If copy_x is True, then the original data is not
        modified.  If False, the original data is modified, and put back before
        the function returns, but small numerical differences may be introduced
        by subtracting and then adding the data mean.

    normalize : boolean, default True
        Normalize the input to have unnit norm.

    Attributes
    ----------

    cluster_centers_ : array, [n_clusters, n_features]
        Coordinates of cluster centers

    labels_ :
        Labels of each point

    inertia_ : float
        Sum of distances of samples to their closest cluster center.
    """

    def __init__(
        self,
        n_clusters=8,
        init="k-means++",
        n_init=10,
        max_iter=300,
        tol=1e-4,
        n_jobs=1,
        verbose=0,
        random_state=None,
        copy_x=True,
        normalize=True,
        p=None,
        side_info=None,
        true_ent2clust=None,
        true_clust2ent=None
    ):
        self.n_clusters = n_clusters
        self.init = init
        self.max_iter = max_iter
        self.tol = tol
        self.n_init = n_init
        self.verbose = verbose
        self.random_state = random_state
        self.copy_x = copy_x
        self.n_jobs = n_jobs
        self.normalize = normalize
        self.p = p
        self.side_info = side_info
        self.true_ent2clust = true_ent2clust
        self.true_clust2ent = true_clust2ent

    def fit(self, X_view_1, X_view_2, sample_weight=None):
        """Compute k-means clustering.

        Parameters
        ----------

        X_view_1 : array-like or sparse matrix, shape=(n_samples, n_features)

        X_view_2 : array-like or sparse matrix, shape=(n_samples, n_features)

        sample_weight : array-like, shape (n_samples,), optional
            The weights for each observation in X. If None, all observations
            are assigned equal weight (default: None)
        """
        if self.normalize:
            X_view_1 = normalize(X_view_1)
            X_view_2 = normalize(X_view_2)

        random_state = check_random_state(self.random_state)

        # TODO: add check that all data is unit-normalized

        self.labels_, self.inertia_, self.n_iter_ = multi_view_spherical_k_means(
            X_view_1,
            X_view_2,
            n_clusters=self.n_clusters,
            sample_weight=sample_weight,
            init=self.init,
            n_init=self.n_init,
            max_iter=self.max_iter,
            verbose=self.verbose,
            tol=self.tol,
            random_state=random_state,
            copy_x=self.copy_x,
            n_jobs=self.n_jobs,
            return_n_iter=True,
            p=self.p,
            side_info=self.side_info,
            true_ent2clust=self.true_ent2clust,
            true_clust2ent=self.true_clust2ent
        )

        return self
