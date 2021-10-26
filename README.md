# harmonic-inference

This is the repository for our ISMIR 2021 paper "A Modular System for the Harmonic Analysis of Musical Scores using a Large Vocabulary".

## Citing
If you use this code, please cite using the following Bibtex:

```
@inproceedings{McLeod:21,
  title={A Modular System for the Harmonic Analysis of Musical Scores using a Large Vocabulary},
  author={McLeod, Andrew and Rohrmeier, Martin},
  booktitle={International Society for Music Information Retrieval Conference {(ISMIR)}},
  year={2021}
}
```

## Installation
1. Clone this repository
2. Set up an environment using your favorite environment manager with python 3, e.g.:
```
conda create -n harmony python=3
conda activate harmony
```
3. Install the package and dependencies with pip:
```
pip install -e .[dev]
```

## Usage

### Running on a MusicXML Score
Given a MusicXML or DCML-style MS3 score, the `annotate.py` script can be used to generate harmonic annotations for it:

MusicXML:
```
python annotate.py -x -i [MusicXML_dir] --checkpoint {checkpoints-best,checkpoints-fh-best} --csm-version {0,1,2}
```

Given a DCML annotation corpus, you must first create aggregated tsv data (see [DCML Pre-processing](#DCML-Pre-processing)). Then, you can use the following command:
```
python annotate.py -i corpus_data --checkpoint {checkpoints-best,checkpoints-fh-best} --csm-version {0,1,2}
```

* `--checkpoint` should point to the models you want to use (pre-trained FH, pre-trained internal, or your own; see [Training](#Training)).
* `--csm-version 0` uses the standard CSM, `1` uses the CSM-I, and `2` uses the CSM-T (which achieved the best performance in our tests).

Other hyperparameters and options can be seen with `python annotate.py -h`.

To use the exact hyperparameter settings from our paper's grid search, add the arguments `--defaults` (for the internal-trained checkpoints `checkpoints-best`) or `--fh-defaults` (for the F-H-trained checkpoints `checkpoints-fh-best`). __You must still set the `--checkpoint` and `--csm-version` manually.__

For example, the best performing model from the internal corpus can be run with: `python annotate.py --checkpoint checkpoints-best --csm-version 2 --defaults -i [input]`

### Data Creation
For training the modules, h5 data files must be created from the raw data.

#### DCML Pre-processing
From a DCML annotation corpus (e.g., any of those listed [here](https://github.com/DCMLab/dcml_corpora)), you must first create aggregated tsv data with the `aggregate_corpus_data.py` script:

```
python aggregate_corpus_data.py --input [input_dir] --output corpus_data
```

Now, `corpus_data` will contain aggregated tsv files for use in model training, annotation, and evaluation.

#### H5 files
To create h5 data files, use the `create_h5_data.py` script.

From a DCML corpus (aggregated as above): `python create_h5_data.py -i corpus_data -h5 h5_data`  
From the Functional Harmony corpus: `python create_h5_data.py -x -i functional-harmony -h5 h5_data`  
* A pre-created version of the F-H data (with default splits and seed) is in the [h5_data-fh](h5_data-fh) directory.

Now, `h5_data` will contain the h5 data files, split into train, test, and validation. Run `python create_h5_data.py -h` for other arguments, like split sizes and random seeding.

### Training
Pre-trained models for the internal data can be found in [checkpoints-best](checkpoints-best).  
Pre-trained models for the Functional Harmony corpus can be found in [checkpoints-fh-best](checkpoints-fh-best).  

You can inspect the hyperparameters and training logs using `tensorboard --logdir [dir]`.

To train new models from scratch, use the `train.py` script.

The models will save by default in the `checkpoints` directory, which can be changed with the `--checkpoint` argument.

For the initial chord model (ICM), with DCML data: `python train.py -m icm -i corpus_data -h5 h5_data`  
For the ICM, with Functional Harmony data: `python train.py -m icm -i [functional-harmony-dir] -h5 h5_data`

For the other models: `python train.py -m {ctm,ccm,csm,ktm,ksm} -h5 h5_data`

Other arguments (GPU training, etc.) are listed with `python train.py -h`

#### Model kwargs
The `--model-kwargs` argument can be used for training models with different dimensionality for a grid search, as well as CSMs and ICMs with different reductions (e.g., CSM-I and CSM-T in the paper). This argument takes a json file name and passes through the values as keyword arguments to the network's `__init__` method.

The json files used for grid search for the results in the paper are in the [model_jsons-grid_search](model_jsons-grid_search) directory.  
The best json files corresponding with the best models from our grid search are in the [model_jsons-best](model_jsons-best) (internal corpus) and [model_jsons-fh-best](model_jsons-fh-best) (FH corpus) directories.
