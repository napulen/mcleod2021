"""Combined models that output a key/chord sequence given an input score, midi, or audio."""
from fractions import Fraction
from typing import List, Tuple, Dict
import logging
import heapq
from collections import defaultdict

from torch.utils.data.dataloader import DataLoader
from tqdm import tqdm
import numpy as np

import harmonic_inference.models.chord_classifier_models as ccm
import harmonic_inference.models.chord_sequence_models as csm
import harmonic_inference.models.chord_transition_models as ctm
import harmonic_inference.models.key_sequence_models as ksm
import harmonic_inference.models.key_transition_models as ktm
from harmonic_inference.data.piece import Chord, Key, Piece
import harmonic_inference.data.datasets as ds


MODEL_CLASSES = {
    'ccm': ccm.SimpleChordClassifier,
    'ctm': ctm.SimpleChordTransitionModel,
    'csm': csm.SimpleChordSequenceModel,
    'ktm': ktm.SimpleKeyTransitionModel,
    'ksm': ksm.SimpleKeySequenceModel,
}


class State:
    """
    The state used during the model's beam search.
    """
    def __init__(
        self,
        chord_changes: List[int] = None,
        key_changes: List[int] = None,
        chords: List[int] = None,
        keys: List[int] = None,
        log_prob: float = 0.0,
        most_recent_chord_log_prob: float = 0.0,
    ):
        """
        [summary]

        Parameters
        ----------
        chord_changes : List[int]
            [description]
        key_changes : List[int]
            [description]
        chords : List[int]
            [description]
        keys : List[int]
            [description]
        log_prob : float
            [description]
        most_recent_chord_log_prob : float
            [description]
        """
        self.chord_changes = [0] if chord_changes is None else chord_changes.copy()
        self.key_changes = [0] if key_changes is None else key_changes.copy()
        self.chords = [] if chords is None else chords.copy()
        self.keys = [] if keys is None else keys.copy()

        self.log_prob = log_prob
        self.most_recent_chord_log_prob = most_recent_chord_log_prob

    def chord_transition(
        self,
        chord: int,
        change_index: int,
        change_log_prob: float,
        chord_log_prob: float,
    ):
        """
        [summary]

        Parameters
        ----------
        chord : int
            [description]
        change_index : int
            [description]
        change_log_prob : float
            [description]
        chord_log_prob : float
            [description]

        Returns
        -------
        new_state : State
            [description]
        """
        return State(
            self.chord_changes + [change_index],
            self.key_changes,
            self.chords + [chord],
            self.keys,
            self.log_prob + change_log_prob + chord_log_prob,
            chord_log_prob,
        )

    def key_transition(self, key: int, log_prob: float):
        """
        [summary]

        Parameters
        ----------
        key : int
            [description]
        log_prob : float
            [description]

        Returns
        -------
        new_state : State
            [description]
        """
        return State(
            self.chord_changes,
            self.key_changes + [self.chord_changes[-2]],
            self.chords,
            self.keys + [key],
            self.log_prob - self.most_recent_chord_log_prob + log_prob,
        )

    def __lt__(self, other):
        return self.log_prob < other.log_prob


class HarmonicInferenceModel:
    """
    A model to perform harmonic inference on an input score, midi, or audio piece.
    """
    def __init__(
        self,
        models: Dict,
        min_change_prob: float = 0.5,
        max_no_change_prob: float = 0.5,
        max_chord_length: Fraction = Fraction(8),
        beam_size: int = 500,
    ):
        """
        Create a new HarmonicInferenceModel from a set of pre-loaded models.

        Parameters
        ----------
        models : Dict
            A dictionary mapping of model components:
                'ccm': A ChordClassifier
                'ctm': A ChordTransitionModel
                'csm': A ChordSequenceModel
                'ktm': A KeyTransitionModel
                'ksm': A KeySequenceModel
        min_change_prob : float
            The minimum probability (from the CTM) on which a chord change can occur.
        max_no_change_prob : float
            The maximum probability (from the CTM) on which a chord is allowed not
            to change.
        max_chord_length : Fraction
            The maximum length for a chord generated by this model.
        beam_size : int
            The beam size to use for decoding with this model.
        """
        for model, model_class in MODEL_CLASSES.items():
            assert model in models.keys(), f"`{model}` not in models dict."
            assert isinstance(models[model], model_class), (
                f"`{model}` in models dict is not of type {model_class.__name__}."
            )

        self.chord_classifier = models['ccm']
        self.chord_sequence_model = models['csm']
        self.chord_transition_model = models['ctm']
        self.key_sequence_model = models['ksm']
        self.key_transition_model = models['ktm']

        # Ensure all types match
        assert self.chord_classifier.INPUT_TYPE == self.chord_transition_model.INPUT_TYPE, (
            "Chord Classifier input type does not match Chord Transition Model input type"
        )
        assert self.chord_classifier.OUTPUT_TYPE == self.chord_sequence_model.CHORD_TYPE, (
            "Chord Classifier output type does not match Chord Sequence Model chord type"
        )
        assert self.chord_sequence_model.CHORD_TYPE == self.key_transition_model.INPUT_TYPE, (
            "Chord Sequence Model chord type does not match Key Transition Model input type"
        )
        assert self.chord_sequence_model.CHORD_TYPE == self.key_sequence_model.INPUT_TYPE, (
            "Chord Sequence Model chord type does not match Key Transition Model input type"
        )

        # Set joint model types
        self.INPUT_TYPE = self.chord_classifier.INPUT_TYPE
        self.CHORD_OUTPUT_TYPE = self.chord_sequence_model.CHORD_TYPE
        self.KEY_OUTPUT_TYPE = self.key_sequence_model.KEY_TYPE

        # Save other params
        assert min_change_prob <= max_no_change_prob, (
            "Undefined chord change behavior on probability range "
            f"({max_no_change_prob}, {min_change_prob})"
        )
        self.min_change_prob = min_change_prob
        self.max_no_change_prob = max_no_change_prob
        self.max_chord_length = max_chord_length
        self.beam_size = beam_size

    def get_harmony(
        self,
        piece: Piece,
    ) -> Tuple[List[Tuple[int, Chord]], List[Tuple[int, Key]]]:
        """
        Run the model on a piece and output its harmony.

        Parameters
        ----------
        piece : Piece
            A Piece to perform harmonic inference on.

        Returns
        -------
        chords : List[Tuple[int, Chord]]
            A List of (index, Chord) tuples containing the Chords of the piece and the
            indexes at which each starts.
        keys : List[Tuple[int, Key]]
            A List of (index, Key) tuples containing the Keys of the piece and the
            indexes at which each starts.
        """
        # Get chord change probabilities (with CTM)
        logging.info("Getting chord change probabilities")
        change_probs = self.get_chord_change_probs(piece)

        # Calculate valid chord ranges and their probabilities
        logging.info("Calculating valid chord ranges")
        chord_ranges, chord_log_probs = self.get_chord_ranges(piece, change_probs)

        # Calculate chord priors for each possible chord range (batched, with CCM)
        logging.info("Classifying chords")
        chord_classifications = self.get_chord_classifications(piece, chord_ranges)

        naive_chords = self.naive_chords(chord_ranges, chord_log_probs, chord_classifications)
        print(naive_chords)

        # Iterative beam search for other modules
        logging.info("Performing iterative beam search")
        chords, keys = self.beam_search(
            piece,
            chord_ranges,
            chord_log_probs,
            chord_classifications,
        )

        return chords, keys

    def get_chord_change_probs(self, piece: Piece) -> List[float]:
        """
        Get the Chord Transition Model's outputs for a given piece.

        Parameters
        ----------
        piece : Piece
            A Piece whose CTM outputs to return.

        Returns
        -------
        change_probs : List[float]
            A List of the chord change probability on each input of the given Piece.
        """
        ctm_dataset = ds.ChordTransitionDataset([piece])
        ctm_loader = DataLoader(
            ctm_dataset,
            batch_size=ds.ChordTransitionDataset.valid_batch_size,
            shuffle=False,
        )

        # CTM keeps each piece as a single input, so will only have 1 batch
        for batch in ctm_loader:
            batch_output, batch_length = self.chord_transition_model.get_output(batch)
            return batch_output[0][:batch_length[0]].numpy()

    def get_chord_ranges(
        self,
        piece: Piece,
        change_probs: List[float],
    ) -> Tuple[List[Tuple[int, int]], List[float]]:
        """
        Get all possible chord ranges and their log-probability, given the chord change
        probabilities for each input in the Piece.

        Parameters
        ----------
        piece : Piece
            The Piece whose chord ranges to return.
        change_probs : List[float]
            The probability of a chord change occurring on each input of the Piece.

        Returns
        -------
        chord_ranges : List[Tuple[int, int]]
            A List of possible chord ranges, as (start, end) tuples representing the start
            (inclusive) and end (exclusive) points of each possible range.
        chord_log_probs : List[float]
            For each chord range, it's log-probability, including its end change, but not its
            start change.
        """
        chord_ranges = []
        chord_log_probs = []
        duration_cache = piece.get_duration_cache()

        # Invalid masks all but first note at each onset position
        first = 0
        invalid = np.full(len(change_probs), False, dtype=bool)
        for i, (prev_note, note) in enumerate(
            zip(piece.get_inputs()[:-1], piece.get_inputs()[1:]),
            start=1,
        ):
            if prev_note.onset == note.onset:
                invalid[i] = True
            else:
                if first != i - 1:
                    change_probs[first] = np.max(change_probs[first:i])
                first = i

        # Log everything vectorized
        change_log_probs = np.log(change_probs)
        no_change_log_probs = np.log(1 - change_probs)

        # Starts is a priority queue so that we don't double-check any intervals
        starts = [0]
        heapq.heapify(starts)

        # Efficient checking if an index exists in the priority queue already
        in_starts = np.full(len(change_log_probs), False, dtype=bool)
        in_starts[0] = True

        while starts:
            start = heapq.heappop(starts)

            running_log_prob = 0.0
            running_duration = Fraction(0.0)
            reached_end = True

            # Detect any next chord change positions
            for index, (change_prob, change_log_prob, no_change_log_prob, duration) in enumerate(
                zip(
                    change_probs[start + 1:],
                    change_log_probs[start + 1:],
                    no_change_log_probs[start + 1:],
                    duration_cache[start:]  # Off-by-one because cache is dur to next note
                ),
                start=start + 1,
            ):
                if invalid[index]:
                    continue

                running_duration += duration
                if running_duration > self.max_chord_length:
                    reached_end = False
                    break

                if change_prob > self.min_change_prob:
                    # Chord change can occur
                    chord_ranges.append((start, index))
                    chord_log_probs.append(running_log_prob + change_log_prob)

                    if not in_starts[index]:
                        heapq.heappush(starts, index)
                        in_starts[index] = True

                    if change_prob > self.max_no_change_prob:
                        # Chord change must occur
                        reached_end = False
                        break

                # No change can occur
                running_log_prob += no_change_log_prob

            # Detect if a chord reaches the end of the piece and add it here if so
            if reached_end:
                chord_ranges.append((start, len(change_probs)))
                chord_log_probs.append(running_log_prob)

        return chord_ranges, chord_log_probs

    def get_chord_classifications(
        self,
        piece: Piece,
        ranges: List[Tuple[int, int]],
    ) -> List[np.array]:
        """
        Generate a chord type prior for each potential chord (from ranges).

        Parameters
        ----------
        piece : Piece
            The Piece for which we want to classify the chords.
        ranges : List[Tuple[int, int]]
            A List of all possible chord ranges as (start, end) for the Piece.

        Returns
        -------
        classifications : List[np.array]
            The chord classification prior for each given range.
        """
        ccm_dataset = ds.ChordClassificationDataset([piece], ranges=[ranges], dummy_targets=True)
        ccm_loader = DataLoader(
            ccm_dataset,
            batch_size=ds.ChordClassificationDataset.valid_batch_size,
            shuffle=False,
        )

        # Get classifications
        classifications = []
        for batch in tqdm(ccm_loader, desc="Classifying chords"):
            classifications.extend(
                [output.numpy() for output in self.chord_classifier.get_output(batch)]
            )

        return classifications

    def naive_chords(
        self,
        chord_ranges: List[Tuple[int, int]],
        chord_log_probs: List[float],
        chord_classifications: List[np.array],
    ) -> List[int]:
        """
        [summary]

        Parameters
        ----------
        chord_ranges : List[Tuple[int, int]]
            [description]
        chord_log_probs : List[float]
            [description]
        chord_classifications : List[np.array]
            [description]

        Returns
        -------
        chords : List[int]
            [description]
        """
        priors = np.zeros((chord_ranges[-1][1], len(chord_classifications[0])))

        for (start, end), prior in tqdm(zip(chord_ranges, chord_classifications)):
            priors[start:end] += prior

        return np.argmax(priors, axis=1)

    def beam_search(
        self,
        piece: Piece,
        chord_ranges: List[Tuple[int, int]],
        chord_log_probs: List[float],
        chord_classifications: List[np.array],
    ) -> Tuple[List[Tuple[int, Chord]], List[Tuple[int, Key]]]:
        """
        Perform a beam search over the given Piece to label its Chords and Keys.

        Parameters
        ----------
        piece : Piece
            The Piece to beam search over.
        chord_ranges : List[Tuple[int, int]]
            A List of possible chord ranges, as (start, end) tuples.
        chord_log_probs : List[float]
            The log probability of each chord ranges in chord_ranges.
        chord_classifications : List[np.array]
            The prior distribution over all chord types for each given chord range.

        Returns
        -------
        chords : List[Tuple[int, Chord]]
            [description]
        keys : List[Tuple[int, Key]]
            [description]
        """
        # Dict mapping start of chord to (end, log_prob, prior) tuple
        chord_ranges_dict = defaultdict(list)
        for (start, end), log_prob, prior in zip(
            chord_ranges,
            chord_log_probs,
            chord_classifications,
        ):
            chord_ranges_dict[start].append((end, log_prob, prior))

        all_states = [[] for _ in range(len(piece.get_inputs()) + 1)]
        all_states[0] = [State()]

        for current_start, current_states in tqdm(
            enumerate(all_states[:-1]),
            desc="Beam searching through inputs",
            total=len(all_states) - 1,
        ):
            if len(current_states) > self.beam_size:
                current_states = sorted(current_states, reverse=True)[:self.beam_size]

            all_states[current_start] = []
            for state in current_states:
                for range_end, range_log_prob, chord_prior in chord_ranges_dict[current_start]:
                    for chord, prior in enumerate(chord_prior):
                        all_states[range_end].append(
                            state.chord_transition(chord, range_end, range_log_prob, prior)
                        )

        # Find and return best state
        best_log_prob, best_state = all_states[-1][0]
        for log_prob, state in all_states[-1][1:]:
            if log_prob > best_log_prob:
                best_log_prob = log_prob
                best_state = state

        return best_state
