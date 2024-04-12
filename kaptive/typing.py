"""
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

from functools import cached_property
from typing import Generator
from itertools import chain
from warnings import catch_warnings

from Bio.Seq import Seq
from Bio.Align import PairwiseAligner
from dna_features_viewer import GraphicFeature, GraphicRecord

from kaptive.database import Database, Locus, Gene
from kaptive.log import warning

# Constants -----------------------------------------------------------------------------------------------------------
_PROTEIN_ALIGNER = PairwiseAligner(scoring='blastp', mode='local')


# Classes -------------------------------------------------------------------------------------------------------------
class TypingResultError(Exception):
    pass


class TypingResult:
    def __init__(
            self, sample_name: str | None, db: Database | None, best_match: Locus | None = None,
            score: float | None = 0, zscore: float | None = 0, pieces: list[LocusPiece] | None = None,
            expected_genes_inside_locus: list[GeneResult] | None = None,
            expected_genes_outside_locus: list[GeneResult] | None = None, missing_genes: list[str] | None = None,
            unexpected_genes_inside_locus: list[GeneResult] | None = None,
            unexpected_genes_outside_locus: list[GeneResult] | None = None, extra_genes: list[GeneResult] | None = None,
            scores: dict[str, dict[str, list[float]]] | None = None, scoring_args: dict | None = None,
            confidence_args: dict | None = None):
        self.sample_name = sample_name or ""
        self.db = db
        self.best_match = best_match
        self.score = score
        self.zscore = zscore
        self.pieces = pieces or []  # Pieces of locus reconstructed from alignments
        self.expected_genes_inside_locus = expected_genes_inside_locus or []  # Genes from best_match
        self.expected_genes_outside_locus = expected_genes_outside_locus or []  # Genes from best_match
        self.missing_genes = missing_genes or []  # Genes from best_match that were not found
        self.unexpected_genes_inside_locus = unexpected_genes_inside_locus or []  # Genes from other loci
        self.unexpected_genes_outside_locus = unexpected_genes_outside_locus or []  # Genes from other loci
        self.extra_genes = extra_genes or []  # in db.extra_genes, ALWAYS outside locus (gene_result.piece == None)
        # self.is_elements = is_elements or []  # in db.is_elements, ALWAYS inside locus (gene_result.piece != None)
        self.scores = scores or {}
        self.scoring_args = scoring_args or {}
        self.confidence_args = confidence_args or {}

    def __len__(self):
        return sum(len(i) for i in self.pieces) if self.pieces else 0

    def __iter__(self):
        return chain(self.expected_genes_inside_locus, self.unexpected_genes_inside_locus,
                     self.expected_genes_outside_locus, self.unexpected_genes_outside_locus, self.extra_genes,
                     # self.is_elements
                     )

    def add_gene_result(self, gene_result: GeneResult):
        if gene_result.neighbour_left:  # If gene_result.neighbour_left is not None, the gene is not the first gene
            gene_result.neighbour_left.neighbour_right = gene_result
        if gene_result.piece:  # If gene_result.piece is not None, the gene is inside the locus
            gene_result.piece.add_gene_result(gene_result)
            gene_type = f"{gene_result.gene_type}{'_inside_locus' if gene_result.gene_type.startswith(('expected', 'unexpected')) else ''}"
        else:  # If gene_result.piece is None, the gene is outside the locus
            gene_type = f"{gene_result.gene_type}{'_outside_locus' if gene_result.gene_type.startswith(('expected', 'unexpected')) else ''}"
        getattr(self, gene_type).append(gene_result)  # Add gene result to the appropriate list

    @cached_property  # Cache the percent identity so it is only calculated once
    def percent_identity(self) -> float:
        return (sum(i.percent_identity for i in x) / len(x)) if (x := self.expected_genes_inside_locus) else 0

    @cached_property  # Cache the percent coverage so it is only calculated once
    def percent_coverage(self) -> float:
        return sum(len(i) for i in x) / sum(len(i) for i in self.best_match.genes.values()) * 100 \
            if (x := self.expected_genes_inside_locus) else 0

    @cached_property  # Cache the phenotype so it is only calculated once
    def phenotype(self) -> str:
        gene_phenotypes = set()  # Init set to store gene phenotypes to be used as a key in the phenotypes dict
        for gene in self:
            if gene.gene_type in {'expected_genes', 'extra_genes'}:  # The reported phenotype only considers expected
                gene_phenotypes.add((gene.gene.name, gene.phenotype))  # or extra genes
        # NOTE: The best_match.phenotypes MUST be sorted from largest to smallest gene set to make sure any sets with
        # extra genes are tested first.
        for gene_set, phenotype in self.best_match.phenotypes:  # Get the phenotype from the phenotypes dict
            if len(gene_set) == len(gene_phenotypes.intersection(gene_set)):
                return phenotype
        return self.best_match.type_label  # If no phenotype is found, return the type label

    @cached_property
    def problems(self) -> str:
        problems = f'?{x}' if (x := len(self.pieces)) > 1 else ''
        problems += '-' if self.missing_genes else ''
        problems += '+' if self.unexpected_genes_inside_locus else ''
        problems += '*' if any(
            i.percent_coverage >= 90 and i.below_threshold for i in self.expected_genes_inside_locus) else ''
        problems += '!' if any(i.phenotype == "truncated" for i in self) else ''
        return problems

    @cached_property  # Cache the confidence so it is only calculated once
    def confidence(self) -> str:
        percent_expected_genes = len(self.expected_genes_inside_locus) / len(self.best_match.genes) * 100
        other_genes = len([i for i in self.unexpected_genes_inside_locus if not i.phenotype == "truncated"])
        if not self.confidence_args['allow_below_threshold'] and "*" in self.problems:
            return "Untypeable"
        if len(self.pieces) == 1:  # If there is only one piece
            if not self.missing_genes and not other_genes:
                return "Typeable"
        else:  # If there are multiple pieces
            if other_genes <= self.confidence_args['max_other_genes'] and \
                    percent_expected_genes >= self.confidence_args['percent_expected_genes']:
                return "Typeable"
        return "Untypeable"

    def as_fasta(self) -> str:
        """Returns a fasta-formatted nucleotide sequence of the locus with a newline character at the end."""
        return "".join(i.as_fasta() for i in self.pieces)

    def as_gene_fasta(self) -> str:
        """Returns a fasta-formatted nucleotide sequence of the locus genes with a newline character at the end."""
        return "".join(i.as_fasta() for i in self)

    def as_protein_fasta(self) -> str:
        """Returns a fasta-formatted protein sequence of the locus genes with a newline character at the end."""
        return "".join(i.as_protein_fasta() for i in self)

    def as_GraphicRecord(self) -> GraphicRecord:
        features, start = [], 0
        for piece in self.pieces:
            features.extend(piece.as_GraphicFeatures(start))
            start += len(piece)
        return GraphicRecord(sequence_length=self.__len__(), first_index=0, features=features,
                             sequence=[p.sequence for p in self.pieces])

    def as_table(self, debug: bool = False, max_scores: int = 2) -> str:
        return '\t'.join(
            [
                self.sample_name, self.best_match.name, self.phenotype, self.confidence, self.problems,
                f"{self.percent_identity:.2f}%", f"{self.percent_coverage:.2f}%",
                f"{self.__len__() - len(self.best_match)} bp" if len(self.pieces) == 1 else 'n/a',
                f"{(x := len({i.gene.gene_name for i in self.expected_genes_inside_locus}))} / {(y := len(self.best_match.genes))} ({100 * x / y:.2f}%)",
                ';'.join(str(i) for i in x) if (x := self.expected_genes_inside_locus) else '',
                ';'.join(self.missing_genes), f"{len(x := self.unexpected_genes_inside_locus)}",
                ';'.join(str(i) for i in x) if x else '',
                f"{len(x := self.expected_genes_outside_locus)} / {(y := len(self.best_match.genes))} ({100 * len(x) / y:.2f}%)",
                ';'.join(str(i) for i in x) if x else '',
                f"{len(x := self.unexpected_genes_outside_locus + self.extra_genes)}",
                ';'.join(str(i) for i in x) if x else '',
                ';'.join(str(i) for i in filter(lambda x: x.phenotype == "truncated", self)),
            ] + ([] if not debug else [  # Add debug columns if debug is True
                ';'.join([str(i) for i in self.extra_genes]), ';'.join(i.__repr__() for i in self.pieces),
                f"{self.score:.1f}", f"{self.zscore:.1f}",
                ';'.join(
                    f"{s}_{w}:{'|'.join(f'{l},{x:.4f},{y:.4f}' for l, x, y in self.scores[s][w][:max_scores])}" for s in
                    self.scores for w in self.scores[s]
                ),
                ';'.join(f"{k}={v}" for k, v in self.scoring_args.items()),
                ';'.join(f"{k}={v}" for k, v in self.confidence_args.items())
            ])
        ) + "\n"

    @classmethod
    def from_dict(cls, d: dict, db: Database) -> TypingResult:
        if not (best_match := db.loci.get(d['best_match'])):
            raise TypingResultError(f"Best match {d['best_match']} not found in database")
        self = TypingResult(
            sample_name=d['sample_name'], db=db, best_match=best_match, score=float(d['score']),
            zscore=float(d['zscore']), missing_genes=d['missing_genes'])

        self.pieces = [LocusPiece.from_dict(i, result=self) for i in d['pieces']]
        pieces, gene_results = {i.__repr__(): i for i in self.pieces}, {}
        for gene_type in ['expected_genes_inside_locus', 'unexpected_genes_inside_locus',
                          'expected_genes_outside_locus',
                          'unexpected_genes_outside_locus', 'extra_genes']:
            for gene_result in d[gene_type]:
                gene_result = GeneResult.from_dict(gene_result, result=self)
                gene_result.piece = pieces.get(gene_result.piece.__repr__()) if gene_result.piece else None
                gene_result.gene = db.genes.get(gene_result.gene)
                gene_results[gene_result.__repr__()] = gene_result

        for gene_result in gene_results.values():
            gene_result.neighbour_left = gene_results.get(gene_result.neighbour_left.__repr__())
            gene_result.neighbour_right = gene_results.get(gene_result.neighbour_right.__repr__())
            self.add_gene_result(gene_result)
        return self

    def as_dict(self) -> dict:
        return {
            i: str(getattr(self, i)) for i in
            ['score', 'zscore', 'sample_name', 'best_match', 'percent_identity', 'percent_coverage',
             'confidence', 'phenotype', 'problems']
        } | {
            'pieces': [i.as_dict() for i in self.pieces],
            'expected_genes_inside_locus': [i.as_dict() for i in self.expected_genes_inside_locus],
            'expected_genes_outside_locus': [i.as_dict() for i in self.expected_genes_outside_locus],
            'unexpected_genes_inside_locus': [i.as_dict() for i in self.unexpected_genes_inside_locus],
            'unexpected_genes_outside_locus': [i.as_dict() for i in self.unexpected_genes_outside_locus],
            'extra_genes': [i.as_dict() for i in self.extra_genes], 'missing_genes': self.missing_genes
        }


class LocusPieceError(Exception):
    pass


class LocusPiece:
    def __init__(self, id: str | None = None, result: TypingResult | None = None, start: int | None = 0,
                 end: int | None = 0, strand: str | None = None, sequence: Seq | None = None,
                 expected_genes: list[GeneResult] | None = None, unexpected_genes: list[GeneResult] | None = None,
                 # is_elements: list[GeneResult] | None = None
                 ):
        self.id = id or ""
        self.result = result
        self.start = start
        self.end = end
        self.strand = strand or "unknown"
        self.sequence = sequence or Seq("")
        self.expected_genes = expected_genes or []  # Genes from best_match
        self.unexpected_genes = unexpected_genes or []  # Genes that were found from other loci
        # self.is_elements = is_elements or []  # Specifically db.is_elements

    def __len__(self):
        return self.end - self.start

    def __iter__(self):
        return chain(self.expected_genes, self.unexpected_genes)

    def __str__(self):
        return self.id

    def __repr__(self):
        return f"{self.id}:{self.start}-{self.end}{self.strand}"

    @classmethod
    def from_dict(cls, d: dict, **kwargs) -> LocusPiece:
        return cls(id=d['id'], start=int(d['start']), end=int(d['end']), strand=d['strand'],
                   sequence=Seq(d['sequence']), **kwargs)

    def as_dict(self) -> dict:
        return {
            'id': self.id, 'start': str(self.start), 'end': str(self.end), 'strand': self.strand,
            'sequence': str(self.sequence)
        }

    def as_fasta(self) -> str:
        return f">{self.result.sample_name}|{self.id}:{self.start}-{self.end}{self.strand}\n{self.sequence}\n"

    def add_gene_result(self, gene_result: GeneResult):
        if gene_result.start < self.start:  # Update start and end if necessary
            self.start = gene_result.start
        if gene_result.end > self.end:
            self.end = gene_result.end
        getattr(self, gene_result.gene_type).append(gene_result)

    def as_GraphicFeatures(self, relative_start: int = 0) -> Generator[GraphicFeature, None, None]:
        start, end = relative_start, relative_start + len(self)
        yield GraphicFeature(start=start, end=end, strand=1, thickness=20, color='tab:blue', label=str(self))
        for gene in self:  # Get relative gene start within piece
            gene_start = start + (gene.start - self.start) if gene.strand == "+" else end - (gene.end - self.start)
            gene_end = start + (gene.end - self.start) if gene.strand == "+" else end - (gene.start - self.start)
            if self.strand == "+":
                strand = gene.gene.strand if gene.strand == gene.gene.strand else gene.strand
            else:
                strand = gene.gene.strand if gene.strand != gene.gene.strand else gene.strand
            yield GraphicFeature(
                start=gene_start, end=gene_end,
                strand=0 if gene.phenotype == "truncated" or gene.partial else 1 if strand == "+" else -1,
                color=("green" if gene.gene_type == 'expected_genes' else "orange", gene.percent_identity / 100),
                linecolor='red' if gene.below_threshold else "yellow" if gene.phenotype == "truncated" else 'black',
                legend_text=gene.gene_type, label=str(gene)
            )


class GeneResultError(Exception):
    pass


class GeneResult:
    """
    Class to store alignment results for a single gene in a locus for either a ReadResult or a AssemblyResult.
    """

    def __init__(self, id: str | None = None, gene: Gene | None = None, result: TypingResult | None = None,
                 piece: LocusPiece | None = None, start: int | None = 0, end: int | None = 0, strand: str | None = None,
                 neighbour_left: GeneResult | None = None, neighbour_right: GeneResult | None = None,
                 dna_seq: Seq | None = None, protein_seq: Seq | None = None, below_threshold: bool | None = False,
                 phenotype: str | None = None, gene_type: str | None = None, partial: bool | None = False,
                 percent_identity: float | None = 0, percent_coverage: float | None = 0):
        self.id = id or ''  # Refers to contig name for assembly typing
        self.gene = gene
        self.result = result
        self.start = start
        self.end = end
        self.strand = strand
        self.partial = partial
        self.piece = piece  # inside locus if not None
        self.neighbour_left = neighbour_left  # neighbour to the left of the gene
        self.neighbour_right = neighbour_right  # neighbour to the right of the gene
        self.dna_seq = dna_seq or Seq("")
        self.protein_seq = protein_seq or Seq("")
        self.below_threshold = below_threshold
        self.phenotype = phenotype or "present"
        self.gene_type = gene_type or ""
        self.percent_identity = percent_identity
        self.percent_coverage = percent_coverage

    def __repr__(self):
        return f"{self.gene.name} {self.id}:{self.start}-{self.end}{self.strand}"

    def __len__(self):
        return self.end - self.start

    def __str__(self) -> str:
        s = f'{self.gene.name},{self.percent_identity:.2f}%,{self.percent_coverage:.2f}%'
        s += ",partial" if self.partial else ""
        s += ',truncated' if self.phenotype == "truncated" else ""
        s += ",below_id_threshold" if self.below_threshold else ""
        return s

    @classmethod
    def from_dict(cls, d: dict, **kwargs) -> GeneResult:
        return cls(
            id=d['id'], start=int(d['start']), end=int(d['end']), strand=d['strand'], dna_seq=Seq(d['dna_seq']),
            protein_seq=Seq(d['protein_seq']), below_threshold=True if d['below_threshold'] == 'True' else False,
            phenotype=d['phenotype'], gene_type=d['gene_type'], partial=True if d['partial'] == 'True' else False,
            percent_identity=float(d['percent_identity']), percent_coverage=float(d['percent_coverage']),
            # The string attributes below are placeholders for the actual objects, they will be set later
            piece=d['piece'], neighbour_left=d['neighbour_left'], neighbour_right=d['neighbour_right'], gene=d['gene'],
            **kwargs
        )

    def as_dict(self) -> dict:
        return {
            'id': self.id, 'start': str(self.start), 'end': str(self.end), 'strand': self.strand,
            'dna_seq': str(self.dna_seq), 'protein_seq': str(self.protein_seq), 'partial': str(self.partial),
            'below_threshold': str(self.below_threshold), 'phenotype': self.phenotype, 'gene_type': self.gene_type,
            'percent_identity': str(self.percent_identity), 'percent_coverage': str(self.percent_coverage),
            'gene': self.gene.name, 'piece': self.piece.__repr__() if self.piece else '',
            'neighbour_left': self.neighbour_left.__repr__() if self.neighbour_left else '',
            'neighbour_right': self.neighbour_right.__repr__() if self.neighbour_right else '',
        }

    def extract_translation(self, frame: int = 0, **kwargs):
        """
        Extracts the translation from the DNA sequence of the gene result.
        Will also extract the translation from the gene if it is not already stored.
        param frame: 0, 1, or 2, the frame to start translating from.
        param kwargs: Additional keyword arguments to pass to the Bio.Seq.translate method.
        """
        self.gene.extract_translation(**kwargs)  # Extract the translation from the gene if it is not already stored
        if len(self.dna_seq) == 0:  # If the DNA sequence is empty, raise an error
            raise GeneResultError(f'No DNA sequence for {self.__repr__()}')
        with catch_warnings(record=True) as w:
            self.protein_seq = self.dna_seq[frame:].translate(**kwargs)  # Translate the DNA sequence from the frame
            # for i in w:
            #     warning(f"{i.message}: {self.__repr__()}")
        if len(self.protein_seq) == 0:  # If the protein sequence is still empty, raise a warning
            warning(f'No protein sequence for {self.__repr__()}')
        elif len(self.gene.protein_seq) > 0:  # If both sequences are not empty
            alignment = max(_PROTEIN_ALIGNER.align(self.gene.protein_seq, self.protein_seq), key=lambda x: x.score)
            self.percent_identity = alignment.counts().identities / alignment.length * 100
            self.percent_coverage = (len(self.protein_seq) / len(self.gene.protein_seq)) * 100
            if not self.partial and self.percent_coverage < 95:  # If the protein sequence less than 95% of reference
                self.phenotype = "truncated"  # Set the phenotype to truncated

    def as_fasta(self) -> str:
        """Returns a fasta-formatted nucleotide sequence with a newline character at the end."""
        if len(self.dna_seq) == 0:
            warning(f'No DNA sequence for {self}')
            return ""
        return (f'>{self.gene.name} {self.result.sample_name}|{self.id}:{self.start}-{self.end}{self.strand}\n'
                f'{self.dna_seq}\n')

    def as_protein_fasta(self) -> str:
        """Returns a fasta-formatted protein sequence with a newline character at the end."""
        if len(self.protein_seq) == 0:
            warning(f'No protein sequence for {self.__repr__()}')
            return ""
        return (f'>{self.gene.name} {self.result.sample_name}|{self.id}:{self.start}-{self.end}{self.strand}\n'
                f'{self.protein_seq}\n')
