#! /usr/bin/env python

"""
Merge two or more concentration flat files with a single row for each metabolite
and empty entries for missing concentration data.

Usage with paths to tsv files to merge:
        ./merge_files [TSV1 TSV2 ...]
"""

import io
import os
import sys
import time


from wholecell.io import tsv


FILE_LOCATION = os.path.realpath(os.path.dirname(__file__))
OUTPUT_FILE = os.path.join(FILE_LOCATION, "metabolite_concentrations.tsv")


def load_conc(filename: str) -> tuple[str, dict[str, str]]:
    """
    Load concentration data from a tsv file.  First column should be metabolite
    ID and second column should be concentration.  Does not handle more than
    one concentration column at this point.

    Args:
        filename: path to concentration tsv file

    Returns:
        label: header describing the concentration data
        conc: metabolite ID to concentration
    """

    conc: dict[str, str] = {}
    with io.open(filename, "rb") as f:
        reader = tsv.reader(f)

        headers = next(reader)
        while headers[0].startswith("#"):
            headers = next(reader)
        label = headers[1]

        for line in reader:
            mol_id = line[0].strip('# "')
            mol_conc = line[1]
            if mol_conc:
                conc[mol_id] = mol_conc

    return label, conc


def save_conc(conc: list[tuple[str, dict[str, str]]]):
    """
    Save combined concentration data with blank entries for metabolites with
    unknown concentrations.

    Args:
            conc: entries with header description and metabolite ID to concentration mapping
    """

    mets = {m for c in conc for m in c[1]}
    headers = ['"Metabolite"'] + ['"{}"'.format(c[0]) for c in conc]

    with io.open(OUTPUT_FILE, "wb") as f:
        writer = tsv.writer(f, quotechar="'", lineterminator="\n")
        writer.writerow(
            ["# Created with {} on {}".format(" ".join(sys.argv), time.ctime())]
        )
        writer.writerow(headers)
        for m in sorted(mets):
            writer.writerow(['"{}"'.format(m)] + [c[1].get(m, "NaN") for c in conc])


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise ValueError(
            "Expecting two or more files to merge. {} [TSV1 TSV2 ...]".format(
                sys.argv[0]
            )
        )

    conc_ = [load_conc(f) for f in sys.argv[1:]]
    save_conc(conc_)
