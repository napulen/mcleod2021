"""Utility functions for evaluating model outputs."""
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Union

import numpy as np
import pandas as pd

import harmonic_inference.utils.harmonic_utils as hu
from harmonic_inference.data.data_types import (
    NO_REDUCTION,
    TRIAD_REDUCTION,
    ChordType,
    KeyMode,
    PitchType,
)
from harmonic_inference.data.piece import Piece
from harmonic_inference.models.joint_model import State
from ms3 import Parse


def get_results_df(
    piece: Piece,
    state: State,
    output_root_type: PitchType,
    output_tonic_type: PitchType,
    chord_root_type: PitchType,
    key_tonic_type: PitchType,
) -> pd.DataFrame:
    """
    Evaluate the piece's estimated chords.

    Parameters
    ----------
    piece : Piece
        The piece, containing the ground truth harmonic structure.
    state : State
        The state, containing the estimated harmonic structure.
    chord_root_type : PitchType
        The pitch type used for chord roots.
    key_tonic_type : PitchType
        The pitch type used for key tonics.

    Returns
    -------
    results_df : pd.DataFrame
        A DataFrame containing the results of the given state, with the given settings.
    """
    labels_list = []

    gt_chords = piece.get_chords()
    gt_changes = piece.get_chord_change_indices()
    gt_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(gt_chords, gt_changes, gt_changes[1:]):
        chord = chord.to_pitch_type(chord_root_type)
        gt_chord_labels[start:end] = chord.get_one_hot_index(
            relative=False, use_inversion=True, pad=False
        )
    gt_chord_labels[gt_changes[-1] :] = (
        gt_chords[-1]
        .to_pitch_type(chord_root_type)
        .get_one_hot_index(relative=False, use_inversion=True, pad=False)
    )

    chords, changes = state.get_chords()
    estimated_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(chords, changes[:-1], changes[1:]):
        root, chord_type, inv = hu.get_chord_from_one_hot_index(chord, output_root_type)
        root = hu.get_pitch_from_string(
            hu.get_pitch_string(root, output_root_type), chord_root_type
        )
        chord = hu.get_chord_one_hot_index(chord_type, root, chord_root_type, inversion=inv)
        estimated_chord_labels[start:end] = chord

    gt_keys = piece.get_keys()
    gt_changes = piece.get_key_change_input_indices()
    gt_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(gt_keys, gt_changes, gt_changes[1:]):
        key = key.to_pitch_type(key_tonic_type)
        gt_key_labels[start:end] = key.get_one_hot_index()
    gt_key_labels[gt_changes[-1] :] = gt_keys[-1].to_pitch_type(key_tonic_type).get_one_hot_index()

    keys, changes = state.get_keys()
    estimated_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(keys, changes[:-1], changes[1:]):
        tonic, mode = hu.get_key_from_one_hot_index(key, output_tonic_type)
        tonic = hu.get_pitch_from_string(
            hu.get_pitch_string(tonic, output_tonic_type), key_tonic_type
        )
        key = hu.get_key_one_hot_index(mode, tonic, key_tonic_type)
        estimated_key_labels[start:end] = key

    chord_label_list = hu.get_chord_label_list(chord_root_type, use_inversions=True)
    key_label_list = hu.get_key_label_list(key_tonic_type)

    for duration, est_chord_label, gt_chord_label, est_key_label, gt_key_label in zip(
        piece.get_duration_cache(),
        estimated_chord_labels,
        gt_chord_labels,
        estimated_key_labels,
        gt_key_labels,
    ):
        if duration == 0:
            continue

        labels_list.append(
            {
                "gt_key": key_label_list[gt_key_label],
                "gt_chord": chord_label_list[gt_chord_label],
                "est_key": key_label_list[est_key_label],
                "est_chord": chord_label_list[est_chord_label],
                "duration": duration,
            }
        )

    return pd.DataFrame(labels_list)


def evaluate_chords(
    piece: Piece,
    state: State,
    pitch_type: PitchType,
    use_inversion: bool = True,
    reduction: Dict[ChordType, ChordType] = NO_REDUCTION,
) -> float:
    """
    Evaluate the piece's estimated chords.

    Parameters
    ----------
    piece : Piece
        The piece, containing the ground truth harmonic structure.
    state : State
        The state, containing the estimated harmonic structure.
    pitch_type : PitchType
        The pitch type used for chord roots.
    use_inversion : bool
        True to use inversion when checking the chord type. False to ignore inversion.
    reduction : Dict[ChordType, ChordType]
        A reduction to reduce chord types to another type.

    Returns
    -------
    accuracy : float
        The average accuracy of the state's chord estimates for the full duration of
        the piece.
    """
    gt_chords = piece.get_chords()
    gt_changes = piece.get_chord_change_indices()
    gt_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(gt_chords, gt_changes, gt_changes[1:]):
        gt_labels[start:end] = chord.get_one_hot_index(
            relative=False, use_inversion=True, pad=False
        )
    gt_labels[gt_changes[-1] :] = gt_chords[-1].get_one_hot_index(
        relative=False, use_inversion=True, pad=False
    )

    chords, changes = state.get_chords()
    estimated_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(chords, changes[:-1], changes[1:]):
        estimated_labels[start:end] = chord

    accuracy = 0.0
    for duration, est_label, gt_label in zip(
        piece.get_duration_cache(),
        estimated_labels,
        gt_labels,
    ):
        if duration == 0:
            continue

        gt_root, gt_chord_type, gt_inversion = hu.get_chord_from_one_hot_index(
            gt_label, pitch_type, use_inversions=True
        )

        est_root, est_chord_type, est_inversion = hu.get_chord_from_one_hot_index(
            est_label, pitch_type, use_inversions=True
        )

        distance = get_chord_distance(
            gt_root,
            gt_chord_type,
            gt_inversion,
            est_root,
            est_chord_type,
            est_inversion,
            use_inversion=use_inversion,
            reduction=reduction,
        )
        accuracy += (1.0 - distance) * duration

    return accuracy / np.sum(piece.get_duration_cache())


def get_chord_distance(
    gt_root: int,
    gt_chord_type: ChordType,
    gt_inversion: int,
    est_root: int,
    est_chord_type: ChordType,
    est_inversion: int,
    use_inversion: bool = True,
    reduction: Dict[ChordType, ChordType] = NO_REDUCTION,
) -> float:
    """
    Get the distance from a ground truth chord to an estimated chord.

    Parameters
    ----------
    gt_root : int
        The root pitch of the ground truth chord.
    gt_chord_type : ChordType
        The chord type of the ground truth chord.
    gt_inversion : int
        The inversion of the ground truth chord.
    est_root : int
        The root pitch of the estimated chord.
    est_chord_type : ChordType
        The chord type of the estimated chord.
    est_inversion : int
        The inversion of the estimated chord.
    use_inversion : bool
        True to use inversion when checking the chord type. False to ignore inversion.
    reduction : Dict[ChordType, ChordType]
        A reduction to reduce chord types to another type.

    Returns
    -------
    distance : float
        A distance between 0 (completely correct), and 1 (completely incorrect).
    """
    gt_chord_type = reduction[gt_chord_type]
    est_chord_type = reduction[est_chord_type]

    if not use_inversion:
        gt_inversion = 0
        est_inversion = 0

    if gt_root == est_root and gt_chord_type == est_chord_type and gt_inversion == est_inversion:
        return 0.0

    return 1.0


def evaluate_keys(
    piece: Piece,
    state: State,
    pitch_type: PitchType,
    tonic_only: bool = False,
) -> float:
    """
    Evaluate the piece's estimated keys.

    Parameters
    ----------
    piece : Piece
        The piece, containing the ground truth harmonic structure.
    state : State
        The state, containing the estimated harmonic structure.
    pitch_type : PitchType
        The pitch type used for key tonics.
    tonic_only : bool
        True to only evaluate the tonic pitch. False to also take mode into account.

    Returns
    -------
    accuracy : float
        The average accuracy of the state's key estimates for the full duration of
        the piece.
    """
    gt_keys = piece.get_keys()
    gt_changes = piece.get_key_change_input_indices()
    gt_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(gt_keys, gt_changes, gt_changes[1:]):
        gt_labels[start:end] = key.get_one_hot_index()
    gt_labels[gt_changes[-1] :] = gt_keys[-1].get_one_hot_index()

    keys, changes = state.get_keys()
    estimated_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(keys, changes[:-1], changes[1:]):
        estimated_labels[start:end] = key

    accuracy = 0.0
    for duration, est_label, gt_label in zip(
        piece.get_duration_cache(),
        estimated_labels,
        gt_labels,
    ):
        if duration == 0:
            continue

        gt_tonic, gt_mode = hu.get_key_from_one_hot_index(int(gt_label), pitch_type)
        est_tonic, est_mode = hu.get_key_from_one_hot_index(int(est_label), pitch_type)

        distance = get_key_distance(
            gt_tonic,
            gt_mode,
            est_tonic,
            est_mode,
            tonic_only=tonic_only,
        )
        accuracy += (1.0 - distance) * duration

    return accuracy / np.sum(piece.get_duration_cache())


def get_key_distance(
    gt_tonic: int,
    gt_mode: KeyMode,
    est_tonic: int,
    est_mode: KeyMode,
    tonic_only: bool = False,
) -> float:
    """
    Get the distance from one key to another.

    Parameters
    ----------
    gt_tonic : int
        The tonic pitch of the ground truth key.
    gt_mode : KeyMode
        The mode of the ground truth key.
    est_tonic : int
        The tonic pitch of the estimated key.
    est_mode : KeyMode
        The mode of the estimated key.
    tonic_only : bool
        True to only evaluate the tonic pitch. False to also take mode into account.

    Returns
    -------
    distance : float
        The distance between the estimated and ground truth keys.
    """
    if tonic_only:
        return 0.0 if gt_tonic == est_tonic else 1.0

    return 0.0 if gt_tonic == est_tonic and gt_mode == est_mode else 1.0


def evaluate_chords_and_keys_jointly(
    piece: Piece,
    state: State,
    root_type: PitchType,
    tonic_type: PitchType,
    use_inversion: bool = True,
    chord_reduction: Dict[ChordType, ChordType] = NO_REDUCTION,
    tonic_only: bool = False,
) -> float:
    """
    Evaluate the state's combined chords and keys.

    Parameters
    ----------
    piece : Piece
        The piece, containing the ground truth harmonic structure.
    state : State
        The state, containing the estimated harmonic structure.
    root_type : PitchType
        The pitch type used for chord roots.
    tonic_type : PitchType
        The pitch type used for key tonics.
    use_inversion : bool
        True to use inversion when checking the chord type. False to ignore inversion.
    chord_reduction : Dict[ChordType, ChordType]
        A reduction to reduce chord types to another type.
    tonic_only : bool
        True to only evaluate the key's tonic pitch. False to also take mode into account.

    Returns
    -------
    accuracy : float
        The average accuracy of the state's joint chord and key estimates for the full
        duration of the piece.
    """
    gt_chords = piece.get_chords()
    gt_changes = piece.get_chord_change_indices()
    gt_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(gt_chords, gt_changes, gt_changes[1:]):
        gt_chord_labels[start:end] = chord.get_one_hot_index(
            relative=False, use_inversion=True, pad=False
        )
    gt_chord_labels[gt_changes[-1] :] = gt_chords[-1].get_one_hot_index(
        relative=False, use_inversion=True, pad=False
    )

    chords, changes = state.get_chords()
    estimated_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(chords, changes[:-1], changes[1:]):
        estimated_chord_labels[start:end] = chord

    gt_keys = piece.get_keys()
    gt_changes = piece.get_key_change_input_indices()
    gt_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(gt_keys, gt_changes, gt_changes[1:]):
        gt_key_labels[start:end] = key.get_one_hot_index()
    gt_key_labels[gt_changes[-1] :] = gt_keys[-1].get_one_hot_index()

    keys, changes = state.get_keys()
    estimated_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(keys, changes[:-1], changes[1:]):
        estimated_key_labels[start:end] = key

    accuracy = 0.0
    for duration, est_chord_label, gt_chord_label, est_key_label, gt_key_label in zip(
        piece.get_duration_cache(),
        estimated_chord_labels,
        gt_chord_labels,
        estimated_key_labels,
        gt_key_labels,
    ):
        if duration == 0:
            continue

        gt_root, gt_chord_type, gt_inversion = hu.get_chord_from_one_hot_index(
            gt_chord_label, root_type, use_inversions=True
        )

        est_root, est_chord_type, est_inversion = hu.get_chord_from_one_hot_index(
            est_chord_label, root_type, use_inversions=True
        )

        chord_distance = get_chord_distance(
            gt_root,
            gt_chord_type,
            gt_inversion,
            est_root,
            est_chord_type,
            est_inversion,
            use_inversion=use_inversion,
            reduction=chord_reduction,
        )

        gt_tonic, gt_mode = hu.get_key_from_one_hot_index(int(gt_key_label), tonic_type)
        est_tonic, est_mode = hu.get_key_from_one_hot_index(int(est_key_label), tonic_type)

        key_distance = get_key_distance(
            gt_tonic,
            gt_mode,
            est_tonic,
            est_mode,
            tonic_only=tonic_only,
        )

        similarity = (1.0 - chord_distance) * (1.0 - key_distance)
        accuracy += similarity * duration

    return accuracy / np.sum(piece.get_duration_cache())


def get_label_df(
    state: State,
    piece: Piece,
    root_type: PitchType,
    tonic_type: PitchType,
) -> pd.DataFrame:
    """
    Get a df containing the labels of the given state.

    Parameters
    ----------
    piece : Piece
        The piece, containing the ground truth harmonic structure.
    state : State
        The state, containing the estimated harmonic structure.
    root_type : PitchType
        The pitch type used for chord roots.
    tonic_type : PitchType
        The pitch type used for key tonics.

    Returns
    -------
    label_df : pd.DataFrame
        A DataFrame containing the labels of the given state.
    """
    labels_list = []

    gt_chords = piece.get_chords()
    gt_changes = piece.get_chord_change_indices()
    gt_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(gt_chords, gt_changes, gt_changes[1:]):
        gt_chord_labels[start:end] = chord.get_one_hot_index(
            relative=False, use_inversion=True, pad=False
        )
    gt_chord_labels[gt_changes[-1] :] = gt_chords[-1].get_one_hot_index(
        relative=False, use_inversion=True, pad=False
    )

    chords, changes = state.get_chords()
    estimated_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(chords, changes[:-1], changes[1:]):
        estimated_chord_labels[start:end] = chord

    gt_keys = piece.get_keys()
    gt_changes = piece.get_key_change_input_indices()
    gt_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(gt_keys, gt_changes, gt_changes[1:]):
        gt_key_labels[start:end] = key.get_one_hot_index()
    gt_key_labels[gt_changes[-1] :] = gt_keys[-1].get_one_hot_index()

    keys, changes = state.get_keys()
    estimated_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(keys, changes[:-1], changes[1:]):
        estimated_key_labels[start:end] = key

    chord_label_list = hu.get_chord_label_list(root_type, use_inversions=True)
    key_label_list = hu.get_key_label_list(tonic_type)

    prev_gt_chord_string = None
    prev_gt_key_string = None
    prev_est_key_string = None
    prev_est_chord_string = None

    for duration, note, est_chord_label, gt_chord_label, est_key_label, gt_key_label in zip(
        piece.get_duration_cache(),
        piece.get_inputs(),
        estimated_chord_labels,
        gt_chord_labels,
        estimated_key_labels,
        gt_key_labels,
    ):
        if duration == 0:
            continue

        gt_chord_string = chord_label_list[gt_chord_label]
        gt_key_string = key_label_list[gt_key_label]

        est_chord_string = chord_label_list[est_chord_label]
        est_key_string = key_label_list[est_key_label]

        # No change in labels
        if (
            gt_chord_string == prev_gt_chord_string
            and gt_key_string == prev_gt_key_string
            and est_chord_string == prev_est_chord_string
            and est_key_string == prev_est_key_string
        ):
            continue

        if gt_key_string != prev_gt_key_string or est_key_string != prev_est_key_string:
            gt_tonic, gt_mode = hu.get_key_from_one_hot_index(int(gt_key_label), tonic_type)
            est_tonic, est_mode = hu.get_key_from_one_hot_index(int(est_key_label), tonic_type)

            full_key_distance = get_key_distance(
                gt_tonic,
                gt_mode,
                est_tonic,
                est_mode,
                tonic_only=False,
            )

            if full_key_distance == 0:
                color = "green"

            else:
                partial_key_distance = get_key_distance(
                    gt_tonic,
                    gt_mode,
                    est_tonic,
                    est_mode,
                    tonic_only=True,
                )

                color = "yellow" if partial_key_distance != 1 else "red"

            labels_list.append(
                {
                    "label": est_key_string if est_key_string != prev_est_key_string else "--",
                    "mc": note.onset[0],
                    "mc_onset": note.onset[1],
                    "color_name": color,
                }
            )

        if gt_chord_string != prev_gt_chord_string or est_chord_string != prev_est_chord_string:
            gt_root, gt_chord_type, gt_inversion = hu.get_chord_from_one_hot_index(
                gt_chord_label, root_type, use_inversions=True
            )

            est_root, est_chord_type, est_inversion = hu.get_chord_from_one_hot_index(
                est_chord_label, root_type, use_inversions=True
            )

            full_chord_distance = get_chord_distance(
                gt_root,
                gt_chord_type,
                gt_inversion,
                est_root,
                est_chord_type,
                est_inversion,
                use_inversion=True,
                reduction=NO_REDUCTION,
            )

            if full_chord_distance == 0:
                color = "green"

            else:
                partial_chord_distance = get_chord_distance(
                    gt_root,
                    gt_chord_type,
                    gt_inversion,
                    est_root,
                    est_chord_type,
                    est_inversion,
                    use_inversion=False,
                    reduction=TRIAD_REDUCTION,
                )

                color = "yellow" if partial_chord_distance != 1 else "red"

            labels_list.append(
                {
                    "label": est_chord_string
                    if est_chord_string != prev_est_chord_string
                    else "--",
                    "mc": note.onset[0],
                    "mc_onset": note.onset[1],
                    "color_name": color,
                }
            )

        prev_gt_key_string = gt_key_string
        prev_gt_chord_string = gt_chord_string
        prev_est_key_string = est_key_string
        prev_est_chord_string = est_chord_string

    return pd.DataFrame(labels_list)


def write_labels_to_score(
    labels_dir: Union[str, Path],
    annotations_dir: Union[str, Path],
    basename: str,
):
    """
    Write the annotation labels from a given directory onto a musescore file.

    Parameters
    ----------
    labels_dir : Union[str, Path]
        The directory containing the tsv file containing the model's annotations.
    annotations_dir : Union[str, Path]
        The directory containing the ground truth annotations and MS3 score file.
    basename : str
        The basename of the annotation TSV and the ground truth annotations/MS3 file.
    """
    if isinstance(labels_dir, Path):
        labels_dir = str(labels_dir)

    if isinstance(annotations_dir, Path):
        annotations_dir = str(annotations_dir)

    # Add musescore and tsv suffixes to filename match
    filename_regex = re.compile(basename + "\\.(mscx|tsv)")

    # Parse scores and tsvs
    parse = Parse(annotations_dir, file_re=filename_regex)
    parse.add_dir(labels_dir, key="labels", file_re=filename_regex)
    parse.parse()

    # Write annotations to score
    parse.add_detached_annotations("MS3", "labels")
    parse.attach_labels(staff=2, voice=1, check_for_clashes=False)

    # Write score out to file
    parse.store_mscx(root_dir=labels_dir, suffix="_inferred", overwrite=True)


def average_results(results_path: Union[Path, str], split_on: str = " = ") -> Dict[str, float]:
    """
    Average accuracy values from a file.

    Parameters
    ----------
    results_path : Union[Path, str]
        The file to read results from.
    split_on : str
        The symbol which separates an accuracy's key from its value.

    Returns
    -------
    averages : Dict[str, float]
        A dictionary mapping each accuracy key to its average value.
    """
    averages = defaultdict(list)

    with open(results_path, "r") as results_file:
        for line in results_file:
            if split_on not in line:
                continue

            line_split = line.split(split_on)
            if len(line_split) != 2:
                continue

            key, value = line_split
            key = key.strip()

            if "accuracy" in key:
                averages[key].append(float(value))

    return {key: np.mean(value_list) for key, value_list in averages.items()}


def log_state(state: State, piece: Piece, root_type: PitchType, tonic_type: PitchType):
    """
    Print the full state harmonic structure (in comparison to that of the given piece),
    as debug logging messages.

    Parameters
    ----------
    state : State
        The state whose harmonic structure to print.
    piece : Piece
        The piece with the ground truth harmonic structure, to note where the state's
        structure is incorrect.
    root_type : PitchType
        The pitch type used for the chord roots.
    tonic_type : PitchType
        The pitch type used for the key tonics.
    """
    gt_chords = piece.get_chords()
    gt_changes = piece.get_chord_change_indices()
    gt_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(gt_chords, gt_changes, gt_changes[1:]):
        gt_chord_labels[start:end] = chord.get_one_hot_index(
            relative=False, use_inversion=True, pad=False
        )
    gt_chord_labels[gt_changes[-1] :] = gt_chords[-1].get_one_hot_index(
        relative=False, use_inversion=True, pad=False
    )

    chords, changes = state.get_chords()
    est_chord_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for chord, start, end in zip(chords, changes[:-1], changes[1:]):
        est_chord_labels[start:end] = chord

    gt_keys = piece.get_keys()
    gt_changes = piece.get_key_change_input_indices()
    gt_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(gt_keys, gt_changes, gt_changes[1:]):
        gt_key_labels[start:end] = key.get_one_hot_index()
    gt_key_labels[gt_changes[-1] :] = gt_keys[-1].get_one_hot_index()

    keys, changes = state.get_keys()
    est_key_labels = np.zeros(len(piece.get_inputs()), dtype=int)
    for key, start, end in zip(keys, changes[:-1], changes[1:]):
        est_key_labels[start:end] = key

    chord_label_list = hu.get_chord_label_list(root_type, use_inversions=True)
    key_label_list = hu.get_key_label_list(tonic_type)

    structure = list(zip(gt_key_labels, gt_chord_labels, est_key_labels, est_chord_labels))
    changes = [True] + [
        prev_structure != next_structure
        for prev_structure, next_structure in zip(structure, structure[1:])
    ]

    input_starts = np.array([note.onset for note in piece.get_inputs()])[changes]
    input_ends = list(input_starts[1:]) + [piece.get_inputs()[-1].offset]

    indexes = np.arange(len(changes))[changes]
    durations = [
        np.sum(piece.get_duration_cache()[start:end])
        for start, end in zip(indexes, list(indexes[1:]) + [len(changes)])
    ]

    for gt_chord, est_chord, gt_key, est_key, input_start, input_end, duration in zip(
        np.array(gt_chord_labels)[changes],
        np.array(est_chord_labels)[changes],
        np.array(gt_key_labels)[changes],
        np.array(est_key_labels)[changes],
        input_starts,
        input_ends,
        durations,
    ):
        gt_chord_label = chord_label_list[gt_chord]
        est_chord_label = chord_label_list[est_chord]

        gt_key_label = key_label_list[gt_key]
        est_key_label = key_label_list[est_key]

        logging.debug("%s - %s (duration %s):", input_start, input_end, duration)
        logging.debug("    Estimated structure: %s\t%s", est_key_label, est_chord_label)
        if gt_key_label != est_key_label or gt_chord_label != est_chord_label:
            logging.debug("      Correct structure: %s\t%s", gt_key_label, gt_chord_label)
