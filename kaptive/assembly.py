"""
This module contains classes for interacting with bacterial genome assemblies and contigs and a pipeline
to type them.

Copyright 2023 Tom Stanton (tomdstanton@gmail.com)
https://github.com/klebgenomics/Kaptive

This file is part of Kaptive. Kaptive is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version. Kaptive is distributed
in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
details. You should have received a copy of the GNU General Public License along with Kaptive.
If not, see <https://www.gnu.org/licenses/>.
"""
from __future__ import annotations

from pathlib import Path
from itertools import chain
from json import loads
from subprocess import Popen, PIPE
from typing import TextIO, Pattern, Generator

from Bio.Seq import Seq
import numpy as np

from kaptive.typing import TypingResult, LocusPiece, GeneResult
from kaptive.database import Database, load_database
from kaptive.alignment import Alignment, iter_alns, group_alns, cull_conflicting_alignments, cull_all_conflicting_alignments
from kaptive.misc import parse_fasta, merge_ranges, range_overlap, check_cpus
from kaptive.log import warning, log


# Classes -------------------------------------------------------------------------------------------------------------
class AssemblyError(Exception):
    pass


class Assembly:
    def __init__(self, path: Path | None = None, name: str | None = None, contigs: dict[str: Contig] | None = None):
        self.path = path or Path()
        self.name = name or ''
        self.contigs = contigs or {}

    @classmethod
    def from_path(cls, path: Path, **kwargs) -> Assembly:
        self = cls(path=path, name=path.name.strip('.gz').rsplit('.', 1)[0],
                   contigs={n: Contig(n, d, Seq(s)) for n, d, s in parse_fasta(path, **kwargs)})
        if not self.contigs:
            raise AssemblyError(f"No contigs found in {path}")
        return self

    def __repr__(self):
        return self.name

    def __len__(self):
        return self.path.stat().st_size

    def seq(self, ctg: str, start: int, end: int, strand: str = "+") -> Seq:
        return self.contigs[ctg].seq[start:end] if strand == "+" else self.contigs[ctg].seq[start:end].reverse_complement()

    def map(self, stdin: str, threads: int,) -> Generator[Alignment, None, None]:
        return iter_alns(Popen(f"minimap2 -c -t {threads} {self.path} -".split(), stdin=PIPE, stdout=PIPE, stderr=PIPE
                               ).communicate(stdin.encode())[0])


class ContigError(Exception):
    pass


class Contig(object):
    """
    This class describes a contig in an assembly: the name, length, and sequence.
    """

    def __init__(self, name: str | None = None, desc: str | None = None, seq: Seq | None = Seq('')):
        self.name = name or ''
        self.desc = desc or ''
        self.seq = seq

    def __repr__(self):
        return self.name

    def __len__(self):
        return len(self.seq)


# Functions -----------------------------------------------------------------------------------------------------------
def parse_result(line: str, db: Database, regex: Pattern | None = None, samples: set[str] | None = None,
                 loci: set[str] | None = None) -> TypingResult | None:
    if regex and not regex.search(line):
        return None
    try:
        d = loads(line)
    except Exception as e:
        warning(f"Error parsing JSON line: {e}\n{line}")
        return None
    if samples and d['sample_name'] not in samples:
        return None
    if loci and d['best_match'] not in loci:
        return None
    try:
        return TypingResult.from_dict(d, db)
    except Exception as e:
        warning(f"Error converting JSON line to TypingResult: {e}\n{line}")
        return None


def write_headers(tsv: TextIO | None = None, no_header: bool = False):
    """Write the headers to a file handle."""
    if tsv:
        if tsv.name != '<stdout>' and tsv.tell() != 0:  # If file is path and not already written to
            no_header = True  # Headers already written, useful for running on HPC
        if not no_header:
            tsv.write('\t'.join([
                'Assembly', 'Best match locus', 'Best match type', 'Confidence', 'Problems', 'Identity', 'Coverage',
                'Length discrepancy', 'Expected genes in locus', 'Expected genes in locus, details',
                'Missing expected genes',
                'Other genes in locus', 'Other genes in locus, details', 'Expected genes outside locus',
                'Expected genes outside locus, details', 'Other genes outside locus',
                'Other genes outside locus, details',
                'Truncated genes, details', 'Extra genes', 'Zscore',
            ]) + '\n')


def typing_pipeline(
        assembly: str | Path | Assembly, db: str | Path | Database, threads: None | int = 0,
        min_cov: float | None = 0.5, max_other_genes: int | None = 1, percent_expected_genes: float | None = 50,
        allow_below_threshold: bool | None = False, verbose: bool | None = False) -> TypingResult | None:

    # CHECK ARGS -------------------------------------------------------------------------------------------------------
    threads = check_cpus(threads) if not threads else threads  # Get the number of threads if 0
    if not isinstance(db, Database):  # If the database is a path, parse it
        try:
            db = load_database(db, verbose=verbose)
        except Exception as e:
            warning(f'Error parsing {db}: {e}')
            return None
    if not isinstance(assembly, Assembly) and not (assembly := parse_assembly(assembly, verbose, n_threads=threads)):
        return None

    # ALIGN GENES AND CALCULATE BEST MATCH -----------------------------------------------------------------------------
    scores, idx = np.array(np.zeros(len(db))), {g: i for i, g in enumerate(db.loci.values())}
    genes_found, genes_expected = np.array(np.zeros(len(db))), np.array([len(l.genes) for l in db.loci.values()])
    alignments = []

    for _, alns in group_alns(assembly.map(db.format('ffn'), threads)):  # Group alignments by gene (q)
        if (best := max(alns, key=lambda x: x.mlen)).q_len / best.blen * 100 >= min_cov:  # If best has enough coverage
            scores[(i := idx[best.q.split("_", 1)[0]])] += best.mlen / best.blen  # Add the score to the locus
            genes_found[i] += 1  # Add the gene to the found genes
            alignments.extend(alns)

    if not alignments:
        warning(f'No gene alignments sufficient for typing {assembly}')
        return None

    scores = scores * (genes_found / genes_expected)  # Weight scores by proportion of genes found
    zscores = (scores - np.mean(scores)) / np.std(scores)  # Calculate z-scores
    best_match = list(db.loci.values())[np.argmax(scores)]

    # RECONSTRUCT LOCUS ------------------------------------------------------------------------------------------------
    result = TypingResult(assembly.name, db, best_match, zscores[idx[best_match]])  # Create the result object
    pieces = {  # Align best match seq to assembly, group alignments by ctg and create pieces
        ctg: [LocusPiece(ctg, result, s, e) for s, e in
              merge_ranges([(a.r_st, a.r_en) for a in alns], len(db.largest_locus))]
        for ctg, alns in group_alns(assembly.map(best_match.format('fna'), threads), key='ctg')
    }  # We can't add strand as the pieces may be merged from multiple alignments, we will determine from the genes

    if any(gene.startswith("Extra") for genes, _ in best_match.phenotypes for gene, _ in genes):
        for gene, alns in group_alns(assembly.map(''.join([i.format('ffn') for i in db.extra_loci.values()]), threads)):
            alignments.append(max(alns, key=lambda x: x.mlen))

    # CULL ALIGNMENTS --------------------------------------------------------------------------------------------------
    expected, other = [], []
    [expected.append(a) if a.q in best_match.genes else other.append(a) for a in alignments]
    other = cull_all_conflicting_alignments(other)  # Remove conflicting other alignments
    for i in expected:  # Remove other alignments overlapping best match gene alignments
        other = list(cull_conflicting_alignments(i, other))

    # GET GENE RESULTS -------------------------------------------------------------------------------------------------
    previous_result, piece = None, None  # For determining neighbouring genes
    for a in chain(expected, other):  # For each gene alignment
        if (gene := best_match.genes.get(a.q)):
            gene_type = "expected_genes"
        elif (gene := db.extra_genes.get(a.q)):
            gene_type = "extra_genes"
        else:
            gene = db.genes.get(a.q)
            gene_type = "unexpected_genes"

        piece = next(filter(lambda p: range_overlap((p.start, p.end), (a.r_st, a.r_en)) > 0, pieces.get(a.ctg, [])), None)
        # Test if gene is partial (would overlap with ctg edge if alignment carried through the full gene)
        partial_gene = True if a.r_st <= a.q_st or \
                               a.r_en <= a.q_en or \
                               (a.ctg_len - a.r_en) <= (len(gene) - a.q_en) else False

        gene_result = GeneResult(  # Create gene result and add it to the result
            a.ctg, gene, result, piece, a.r_st, a.r_en, a.strand, previous_result,
            gene_type=gene_type, partial=partial_gene, dna_seq=assembly.seq(a.r_st, a.r_en, a.strand)
        )

        gene_result.compare_translation(  # Extract translation and align to reference
            next((i for i in [0, 1, 2] if (a.q_st + i) / 3 % 1 == 0), 0),  # Get the frame
            table=11, to_stop=True)  # This will also trigger the protein alignment

        gene_result.below_threshold = gene_result.percent_identity < db.gene_threshold  # Check if below threshold
        if not piece and gene_result.below_threshold:  # If below threshold
            continue  # Skip this gene, probably a homologue in another part of the genome
        result.add_gene_result(gene_result)  # Add the gene result to the result to get neighbouring genes
        previous_result = gene_result  # Set the previous gene result to the current result

    # FINALISE PIECES --------------------------------------------------------------------------------------------------
    for ctg, pieces in pieces.items():  # Add sequences to pieces and add them to the result
        for piece in pieces:
            if piece.expected_genes:  # If the piece has expected genes
                piece.strand = "+" if max(i.strand == i.gene.strand for i in piece.expected_genes) else "-"
                # Piece strand is consensus of expected gene strands
                piece.sequence = assembly.seq(ctg, piece.start, piece.end, piece.strand)
                result.pieces.append(piece)  # Add the piece to the result

    # FINALISE RESULT -------------------------------------------------------------------------------------------------
    # Sort the pieces by the sum of the expected gene order to get the expected order of the pieces
    result.pieces.sort(key=lambda x: min(int(i.gene.name.split("_")[1]) for i in x.expected_genes))
    [l.sort(key=lambda x: int(x.gene.name.split("_")[1])) for l in (
        result.expected_genes_inside_locus, result.expected_genes_outside_locus, result.unexpected_genes_inside_locus,
        result.unexpected_genes_outside_locus)]
    result.missing_genes = sorted(list(set(best_match.genes) - {
        i.gene.name for i in chain(result.expected_genes_inside_locus, result.expected_genes_outside_locus)}),
                                  key=lambda x: int(x.split("_")[1]))
    result.get_confidence(allow_below_threshold, max_other_genes, percent_expected_genes)
    log(f"Finished typing {result}", verbose=verbose)
    return result
