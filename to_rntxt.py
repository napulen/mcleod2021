import os
import re
import pandas as pd
import music21


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
    if fraction == "0":
        return 0.0
    elif fraction == "1":
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
    upperNotes = [rootobj.transpose(i) for i in intervals]
    notes = [rootobj] + upperNotes
    chordobj = music21.chord.Chord(notes[inversion:] + notes[:inversion])
    rn = music21.roman.romanNumeralFromChord(chordobj, key)
    return rn


def events_to_rntxt(events):
    key = "C"
    rntxt = ""
    for mm_number, mm in events.items():
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


def main():
    root = "outputs"
    events = {}
    for f in sorted(os.listdir(root)):
        path = os.path.join(root, f)
        print(path)
        df = pd.read_csv(path, sep="\t")
        df["quarterLength"] = df["mc_onset"].apply(fraction_to_quarterlength)
        df["beat"] = df["quarterLength"] + 1
        if df["mc_onset"][0] == "0":
            df["mc"] = df["mc"] + 1
        df["parsed_label"] = df["label"].apply(parse_label)
        events = compute_events(df)
        rntxt = events_to_rntxt(events)
        base, _ = os.path.splitext(f)
        output = os.path.join(root, f"{base}.rntxt")
        with open(output, "w") as f:
            f.write(rntxt)


if __name__ == "__main__":
    main()
