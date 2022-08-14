import subprocess
import os
import time

if __name__ == "__main__":
    log = open("execution_times.log", "w")
    for f in sorted(os.listdir("phd_testset")):
        print(f)
        start = time.time()
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
        end = time.time()
        log.write(f"{f}: {end - start:.2f}\n")
        log.flush()
    log.close()
