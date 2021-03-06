from __future__ import division
import argparse
from datahandler import DataHandler
from config import Config
import tensorflow as tf
from models import CRNN as Model
from models import input_fn
from shutil import rmtree
from os import listdir
import os
from collections import deque
import numpy as np
#import sys

parser = argparse.ArgumentParser(description='Train model baesd on setup of data and model storage, experimental setup, and hyper parameters from YAML-files of profiles.')
parser.add_argument('--config', type=str, default='shhs',
                    help='name of storage configuration profile to use (defaults to local, defined in config.yaml)')
parser.add_argument('--experiment', type=str, default='local',
                    help='name of experimental profile to use (defaults to basis, defined in experiment.yaml)')
parser.add_argument('--model', type=str, default='revised',
                    help='name of model/hyperparameter profile to use (defaults to default, defined in params.yaml)')
parser.add_argument('--verbose', type=bool, default=True,
                    help='set verbosity of tf.logging (defaults to True)')

# this is a string, and I can't remember why i did that, and haven't tested if it could be changed to a bool...
parser.add_argument('--model_memory', type=str, default='True',
                    help='loads model with same profiles if it exists when True, if False deletes existing model (defaults to True)')
parser.add_argument('--hparam', type=str, default=None,
                    help='comma-seperated hparams and value, overrides model parameters from profile (defaults to None), format e.g.: --hparams=learning_rate=0.3.')
parser.add_argument('--export_model', type=bool, default=True,
                    help='serve trained model as predictor when True (defaults to True)')
parser.add_argument('--evaluate_model', type=bool, default=False,
                    help='evaluate model on test data (defaults to False)')
parser.add_argument('--dont_train', type=bool, default=False,
                    help='if True do not train (defaults to False)')
parser.add_argument('--cross_validate', type=int, default=None,
                    help='if True train 5 models and do cross-validation')
args = parser.parse_args()

def serving_input_receiver_fn():
    '''
    Serving input receiver function for tf.Estimator.export_savedmodel
    :return: a TensorServingInputReceiver fitting the input data specified in cf.hparams
    '''
    features = tf.placeholder(shape=[None, cf.hparams.n_epoch_samples, cf.hparams.n_channels], dtype=tf.float32)
    receiver_tensors = features
    return tf.estimator.export.TensorServingInputReceiver(features, receiver_tensors)

if __name__ == "__main__":
    cf = Config(args.config,
                args.experiment,
                args.model,
                args.hparam).get_configs(cross_validate=args.cross_validate)

    DataHandler.setup_partitions(config=cf,
                                 model_memory=eval(args.model_memory),
                                 cross_validate=args.cross_validate)

    run_config = tf.estimator.RunConfig(save_checkpoints_steps=cf.eparams.save_checkpoint_steps,
                                        save_summary_steps=cf.eparams.save_summary_steps)
    model = Model('CRNN', cf.eparams)
    classifier = tf.estimator.Estimator(
            model_fn=lambda features, labels, mode: model(features, labels, mode, cf.hparams),
            model_dir=cf.eparams.ckptdir,
            config=run_config)

    if args.verbose:
        tf.logging.set_verbosity(tf.logging.INFO)

    if not args.dont_train:
        loss_buffer_size = 5
        smoothed_loss_values = np.ones([2])
        test_losses = []
        tolerance = 1e-3
        early_stop_criterion = False
        iteration = 0
        max_steps = 1e4
        train_steps = 100
        eval_steps = 500
        min_iterations = 5
        max_iterations = max_steps // train_steps
        patience = 3
        while True:
            print('Doing iteration {} of train and eval steps.'.format(iteration))

            print('Training:')
            classifier.train(input_fn=lambda: input_fn('train', cf.eparams), steps=train_steps)

            print('Evaluation:')
            eval_results = classifier.evaluate(input_fn=lambda: input_fn('val', cf.eparams), steps=eval_steps)
            print('Evaluational loss result: {}.'.format(eval_results['loss']))

            if iteration == 0:
                l = deque(eval_results['loss']*np.ones([loss_buffer_size]))

            test_losses.append(eval_results['loss'])
            smoothed_loss_values[0] = np.mean(l)
            l.popleft()
            l.append(eval_results['loss'])
            smoothed_loss_values[1] = np.mean(l)
            iteration += 1

            print('    Loss  deque: {}'.format(l))
            print('    Smoothed loss values: {}'.format(smoothed_loss_values))

            if iteration < min_iterations:
                continue
                
            early_stop_criterion = (smoothed_loss_values[1]-smoothed_loss_values[0]) > -tolerance
            if early_stop_criterion:
                patience -= 1
                if patience == 0:
                    print('    Reached early stopping criterion at {} iterations.'.format(iteration))
                    break
                else:
                    print('    Patience reduced to: {}'.format(patience))
            elif patience < 2:
                patience += 1

            if iteration > max_iterations:
                print('    Reached maximum iterations.')
                break
        '''
        train_spec = tf.estimator.TrainSpec(input_fn=lambda: input_fn('train', cf.eparams),
                                            max_steps=cf.eparams.train_steps,
                                            hooks=[])
        eval_spec = tf.estimator.EvalSpec(input_fn=lambda: input_fn('val', cf.eparams),
                                          steps=cf.eparams.eval_steps,
                                          throttle_secs=cf.eparams.throttle_secs,
                                          hooks=[])
        tf.estimator.train_and_evaluate(classifier, train_spec, eval_spec)
        '''
        
    if args.evaluate_model:
        result = classifier.evaluate(input_fn=lambda: input_fn('val', cf.eparams), steps=500)

    if args.export_model: classifier.export_savedmodel(export_dir_base=cf.eparams.ckptdir, serving_input_receiver_fn=serving_input_receiver_fn)
