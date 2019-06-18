import argparse
import os

import tensorflow as tf
import numpy as np
import graphGAN.utils.common as utils
import graphGAN.utils.data as data
import pickle
import graphGAN.models.graphgan as graphgan
import tqdm


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description='Train a GraphGAN',
        usage='trainer.py [<args>] [-h | --help]'
    )

    parser.add_argument("--data_dir", type=str)
    parser.add_argument("--log_dir", type=str)
    parser.add_argument("--parameters", type=str, default="")

    return parser.parse_args(args)


def default_parameters():
    params = tf.contrib.training.HParams(
        data_dir="",
        emb_generator="gen.emb",
        emb_discriminator="dis.emb",
        record="record",
        log_dir="",
        train_edges="train_edges.txt",
        test_edges="test_edges.txt",
        train_trees="train_trees.pkl",
        n_node=1000,
        graph={},
        s_nodes=set(),
        trees={},
        batch_size_gen=64,  # batch size for the generator
        batch_size_dis=64,  # batch size for the discriminator
        lambda_gen=1e-5,  # l2 loss regulation weight for the generator
        lambda_dis=1e-5,  # l2 loss regulation weight for the discriminator
        n_sample_gen=20,  # number of samples for the generator
        lr_gen=1e-3,  # learning rate for the generator
        lr_dis=1e-3,  # learning rate for the discriminator
        window_size=2,
        n_epochs=20,  # number of outer loops
        n_epochs_gen=30,  # number of inner loops for the generator
        n_epochs_dis=30,  # number of inner loops for the discriminator
        gen_interval=30,  # sample new nodes for the generator for every gen_interval iterations
        dis_interval=30,  # sample new nodes for the discriminator for every dis_interval iterations
        update_ratio=1,  # updating ratio when choose the trees
        save_steps=1,
        n_movies=3953,
        n_emb=50,
        top_k=10,
        max_to_save=100,
        pretrain_emb_filename_d="pre_train.emb",
        pretrain_emb_filename_g="pre_train.emb",
        init_emb_d=np.array([0]),
        init_emb_g=np.array([0])
    )

    return params


def import_params(log_dir, params):
    p_name = os.path.join(os.path.abspath(log_dir), 'params.jason')

    if not tf.gfile.Exists(p_name):
        return params

    with tf.gfile.Open(p_name) as fr:
        tf.logging.info("Restoring hyper parameters from %s" % p_name)
        json_str = fr.readline()
        params.parse_json(json_str)

    return params


def override_params(params, args):
    params.data_dir = args.data_dir
    params.log_dir = args.log_dir
    params.parse(args.parameters)

    params.emb_generator = params.log_dir + '/' + params.emb_generator
    params.emb_discriminator = params.log_dir + '/' + params.emb_discriminator
    params.record = params.log_dir + '/' + params.record
    params.train_edges = params.data_dir + '/' + params.train_edges
    params.test_edges = params.data_dir + '/' + params.test_edges
    params.train_trees = params.data_dir + '/' + params.train_trees
    params.pretrain_emb_filename_d = params.data_dir + '/' + params.pretrain_emb_filename_d
    params.pretrain_emb_filename_g = params.data_dir + '/' + params.pretrain_emb_filename_g

    params.n_node, params.graph, params.s_nodes = \
        utils.read_edges(params.train_edges, params.test_edges)

    with open(params.train_trees, 'rb') as fr:
        params.trees = pickle.load(fr)

    if os.path.isfile(params.pretrain_emb_filename_d):
        params.init_emb_d = utils.read_embeddings(params.pretrain_emb_filename_d, params.n_node, params.n_emb)
    else:
        params.init_emb_d = np.random.randn(params.n_node, params.n_emb) / float(params.n_emb)

    if os.path.isfile(params.pretrain_emb_filename_g):
        params.init_emb_g = utils.read_embeddings(params.pretrain_emb_filename_g, params.n_node, params.n_emb)
    else:
        params.init_emb_g = np.random.randn(params.n_node, params.n_emb) / float(params.n_emb)

    return params


def print_variables():
    all_weights = {v.name: v for v in tf.trainable_variables()}
    total_size = 0

    for v_name in sorted(list(all_weights)):
        v = all_weights[v_name]
        tf.logging.info("%s\tshape    %s", v.name.ljust(80),
                        str(v.shape).ljust(20))
        v_size = np.prod(np.array(v.shape.as_list())).tolist()
        total_size += v_size
    tf.logging.info("Total trainable variables size: %d", total_size)


def eval(args):
    tf.logging.set_verbosity(tf.logging.INFO)
    params = default_parameters()
    params = import_params(args.log_dir, params)
    params = override_params(params, args)

    model = graphgan.GraphGAN(params)

    node_id = tf.placeholder(tf.int32, shape=[None])
    node_neighbor_id = tf.placeholder(tf.int32, shape=[None])
    reward = tf.placeholder(tf.float32, shape=[None])
    label = tf.placeholder(tf.float32, shape=[None])

    tf.logging.info("Building generator...")
    _, _, gen_emb = model.build_generator(params, [node_id, node_neighbor_id, reward])
    gen_all_score = tf.matmul(gen_emb, gen_emb, transpose_b=True)
    tf.logging.info("Building discriminator...")
    _, _, dis_emb = model.build_discriminator(params, [node_id, node_neighbor_id, label])
    dis_all_score = tf.matmul(dis_emb, dis_emb, transpose_b=True)

    print_variables()

    saver = tf.train.Saver(max_to_keep=params.max_to_save)

    sess_config = tf.ConfigProto()
    sess_config.gpu_options.allow_growth = True
    init_op = tf.group(tf.global_variables_initializer(), tf.local_variables_initializer())
    sess = tf.Session(config=sess_config)
    sess.run(init_op)

    with open(params.record, 'a+') as fw:
        fw.write('model\tepoch\tprecision\trecall\n')
        for epoch in range(params.n_epochs):
            model_checkpoint_path = params.log_dir + '/' + 'model-%d' % epoch
            tf.logging.info("loading the checkpoint: %s" % model_checkpoint_path)
            saver.restore(sess, model_checkpoint_path)
            gen_all_score_v = sess.run(gen_all_score)
            dis_all_score_v = sess.run(dis_all_score)

            gen_accuracy, gen_recall = model.eval_recommend(gen_all_score_v, params)
            dis_accuracy, dis_recall = model.eval_recommend(dis_all_score_v, params)

            fw.write('gen\t{}\t{:.10f}\t{:.10f}\n'.format(epoch, gen_accuracy, gen_recall))
            fw.write('dis\t{}\t{:.10f}\t{:.10f}\n'.format(epoch, dis_accuracy, dis_recall))


if __name__ == "__main__":
    eval(parse_args())
