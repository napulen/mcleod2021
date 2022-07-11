import subprocess
import os

for f in sorted(os.listdir("phd_testset")):
    if f.endswith(".mxl") or f.endswith(".musicxml"):
        print(f)
        subprocess.call(
            [
                "python",
                "annotate.py",
                "-x",
                "-i",
                f"phd_testset/{f}",
                "--checkpoint",
                "checkpoints-best",
                "--csm-version",
                "2",
                "--defaults",
                "--threads",
                "6",
            ]
        )
