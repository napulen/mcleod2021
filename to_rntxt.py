import os
import pandas as pd
import music21
import numpy as np


quarter_length = {
    "1": 4,
    "2": 2,
    "4": 1,
    "8": 0.5,
    "16": 0.25,
    "32": 0.125,
    "64": 0.0625,
}

quality_intervals = {
    "+": ["M3", "A5"],
    "M": ["M3", "P5"],
    "m": ["m3", "P5"],
    "o": ["m3", "D5"],
    "MM7": ["M3", "P5", "M7"],
    "Mm7": ["M3", "P5", "m7"],
    "mm7": ["m3", "P5", "m7"],
    "%7": ["m3", "D5", "m7"],
    "o7": ["m3", "D5", "D7"],
}


def fraction_to_quarterlength(fraction):
    if fraction in ["0", 0]:
        return 0.0
    elif fraction in ["1", 1]:
        return 4.0
    num, den = fraction.split("/")
    quarterLength = quarter_length[den] * int(num)
    return quarterLength


def parse_label(label):
    if "KeyMode" in label:
        key, mode = label.split(":KeyMode.")
        if mode == "MINOR":
            key = "{}{}".format(key[0].lower(), key[1:])
        return ("key", key)
    else:
        chord, inversion = label.split(", inv:")
        root, quality = chord.split(":")
        root = "{}{}".format(root[0], root[1:].replace("b", "-"))
        return ("chord", (root, quality, int(inversion)))


def compute_events(df):
    events = {}
    for row in df.itertuples():
        mm = row.mc
        beat = row.beat
        type, label = row.parsed_label
        if mm not in events:
            events[mm] = {}
        if beat not in events[mm]:
            events[mm][beat] = {}
        events[mm][beat][type] = label
    return events


def chord_tuple_to_roman_numeral(chord, key):
    root, quality, inversion = chord
    rootobj = music21.note.Note(f"{root}4")
    intervals = quality_intervals[quality]
    notes = [rootobj] + [rootobj.transpose(i) for i in intervals]
    notes[inversion].octave -= 1
    chordobj = music21.chord.Chord(notes)
    rn = music21.roman.romanNumeralFromChord(chordobj, key)
    return rn


def events_to_rntxt(events, ts):
    key = "C"
    rntxt = ""
    for mm_number, mm in events.items():
        newts = ts.get(mm_number, None)
        if newts:
            rntxt += f"\nTime Signature: {newts}\n\n"
        rntxt += f"m{mm_number}"
        for beat_number, beat in mm.items():
            if beat_number.is_integer():
                beat_number = int(beat_number)
            if beat_number != 1:
                rntxt += f" b{beat_number}"
            if "key" in beat:
                key = beat["key"]
                rntxt += f" {beat['key']}:"
            rn = chord_tuple_to_roman_numeral(beat["chord"], key)
            rntxt += f" {rn.figure}"
        rntxt += "\n"
    return rntxt


def _measureNumberShift(m21Score):
    firstMeasure = m21Score.parts[0].measure(0) or m21Score.parts[0].measure(1)
    isAnacrusis = True if firstMeasure.paddingLeft > 0.0 else False
    if isAnacrusis and firstMeasure.number == 1:
        measureNumberShift = -1
    else:
        measureNumberShift = 0
    return measureNumberShift


def retrieve_beats_from_score(m21s, mmshift=0):
    beats = {}
    for n in m21s.recurse().notesAndRests:
        mm = n.measureNumber + mmshift
        offset = n.offset
        beat = n.beat
        if mm not in beats:
            beats[mm] = {}
        if offset not in beats[mm]:
            beats[mm][offset] = round(float(beat), 3)
        else:
            print("WARNING: duplicate offset")
    return beats


def main():
    root_pred = "outputs"
    root_mxl = "phd_testset_chordified"
    events = {}
    for f in sorted(os.listdir(root_pred)):
        path = os.path.join(root_pred, f)
        pathscore = os.path.join(root_mxl, f.replace(".tsv", ".xml"))
        s = music21.converter.parse(pathscore, forceSource=True)
        mmshift = _measureNumberShift(s)
        ts = {
            (ts.measureNumber + mmshift): ts.ratioString
            for ts in s.flat.getElementsByClass("TimeSignature")
        }
        beats = retrieve_beats_from_score(s, mmshift)
        print(path)
        df = pd.read_csv(path, sep="\t")
        # relative offset in measure
        df["offset"] = df["mc_onset"].apply(fraction_to_quarterlength)
        if df["mn_onset"][0] == "0":
            df["mc"] = df["mc"] + 1
        df["beat"] = df.apply(
            lambda row: beats.get(row.mc, {}).get(row.offset, np.nan), axis=1
        )
        # The examples I've debugged, the m21 score is correct,
        # so blame the mistakes on the alignment of the model and drop those annotations
        df.dropna(inplace=True)
        df["parsed_label"] = df["label"].apply(parse_label)
        events = compute_events(df)
        rntxt = events_to_rntxt(events, ts)
        base, _ = os.path.splitext(f)
        output = os.path.join(root_pred, f"{base}.rntxt")
        with open(output, "w") as f:
            f.write(rntxt)


if __name__ == "__main__":
    main()
