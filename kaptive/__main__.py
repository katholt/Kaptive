#!/usr/bin/env python
"""
This is the main entry point for the kaptive package. It is called when the package is run as a script via entry_points.

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

import sys
import re
import argparse
from pathlib import Path

from kaptive.version import __version__
from kaptive.log import bold, quit_with_error
from kaptive.misc import check_python_version, check_programs, get_logo, check_cpus, check_dir, check_file
from kaptive.database import Database, get_database

# Constants -----------------------------------------------------------------------------------------------------------
_ASSEMBLY_HEADERS = [
    'Assembly', 'Best match locus', 'Best match type', 'Confidence', 'Problems', 'Identity', 'Coverage',
    'Length discrepancy', 'Expected genes in locus', 'Expected genes in locus, details', 'Missing expected genes',
    'Other genes in locus', 'Other genes in locus, details', 'Expected genes outside locus',
    'Expected genes outside locus, details', 'Other genes outside locus', 'Other genes outside locus, details',
    'Truncated genes, details'
]
_ASSEMBLY_EXTRA_HEADERS = [
    'Extra genes', 'Contigs', 'Pieces', 'Pieces, details', 'Score', 'Zscore', 'All scores', 'Args'
]


# Functions -----------------------------------------------------------------------------------------------------------
def parse_args(a):
    parser = argparse.ArgumentParser(
        description=get_logo('In silico serotyping'), usage="%(prog)s <command>", add_help=False,
        prog="kaptive", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=f"%(prog)s version {__version__}")

    subparsers = parser.add_subparsers(title=bold('Command'), dest='subparser_name', metavar="")
    assembly_subparser(subparsers)
    # reads_subparser(subparsers)
    extract_subparser(subparsers)
    convert_subparser(subparsers)
    opts = parser.add_argument_group(bold('Other options'), '')
    other_opts(opts)

    if len(a) == 0:  # No arguments, print help message
        parser.print_help(sys.stderr)
        quit_with_error(f'Please specify a command; choose from {{assembly,extract,convert}}')
    if any(x in a for x in {'-v', '--version'}):  # Version message
        print(__version__)
        sys.exit(0)
    if subparser := subparsers.choices.get(a[0], None):  # Check if the first argument is a subparser
        if len(a) == 1:  # Subparser help message
            subparser.print_help(sys.stderr)
            quit_with_error(f'Insufficient arguments for kaptive {a[0]}')
        if any(x in a[1:] for x in {'-h', '--help'}):  # Subparser help message
            subparser.print_help(sys.stderr)
            sys.exit(0)
    elif any(x in a for x in {'-h', '--help'}):  # Help message
        parser.print_help(sys.stderr)
        sys.exit(0)
    else:  # Unknown command
        parser.print_help(sys.stderr)
        quit_with_error(f'Unknown command "{a[0]}"; choose from {{assembly,extract,convert}}')
    return parser.parse_args(a)


def assembly_subparser(subparsers):
    assembly_parser = subparsers.add_parser(
        'assembly', description=get_logo('In silico serotyping of assemblies'),
        epilog=f'kaptive assembly v{__version__}', add_help=False, formatter_class=argparse.RawTextHelpFormatter,
        help='In silico serotyping of assemblies', usage="kaptive assembly <db> <input> [<input> ...] [options]")
    opts = assembly_parser.add_argument_group(bold('Inputs'), "")
    opts.add_argument('db', type=get_database, help='Kaptive database path or keyword')
    opts.add_argument('input', nargs='+', type=check_file, help='Assemblies in fasta(.gz) format')
    opts = assembly_parser.add_argument_group(bold('Output options'), "")
    output_opts(opts)
    opts = assembly_parser.add_argument_group(bold('Alignment options'), "")
    alignment_opts(opts)
    opts = assembly_parser.add_argument_group(bold('Scoring options'), "")
    opts.add_argument("--score", type=str, default='AS', metavar='',
                      help="Alignment metric to use for scoring (default: %(default)s)")
    opts.add_argument("--min-zscore", type=float, metavar='', default=3.0,
                      help="Minimum zscore for confidence (default: %(default)s)")
    opts.add_argument("--weight", type=str, metavar='', default='prop_genes_found',
                      help="Weighting for scoring metric (default: %(default)s)\n"
                           " - none: No weighting\n"
                           " - locus_length: length of the locus\n"
                           " - genes_expected: # of genes expected in the locus\n"
                           " - genes_found: # of genes found in the locus\n"
                           " - prop_genes_found: genes_found / genes_expected")
    opts = assembly_parser.add_argument_group(bold('Locus reconstruction options'), "")
    opts.add_argument("--gene-threshold", type=float, metavar='',
                      help="Species-level locus gene identity threshold (default: database specific)")
    opts.add_argument('--min-cov', type=float, required=False, default=50.0, metavar='',
                      help='Minimum %%coverage for gene alignment to be used for scoring (default: %(default)s)')
    opts = assembly_parser.add_argument_group(bold('Database options'), "")
    db_opts(opts)
    # opts.add_argument('--is-seqs', type=check_file, metavar='',
    #                   help='Fasta file of IS element sequences to include in the database (default: None)')
    opts = assembly_parser.add_argument_group(bold('Other options'), "")
    other_opts(opts)


# def reads_subparser(subparsers):
#     assembly_parser = subparsers.add_parser(
#         'reads', description=bold_cyan(LOGO + f"\n{'In silico serotyping of reads' : ^43}"),
#         epilog=f'kaptive reads v{__version__}', add_help=False, formatter_class=argparse.RawTextHelpFormatter,
#         help='In silico serotyping of reads', usage="kaptive reads <db> <reads> [<reads> ...] [options]")
#     opts = assembly_parser.add_argument_group(bold('Inputs'), "")
#     opts.add_argument('db', type=get_database, help='Kaptive database path or keyword')
#     opts.add_argument('reads', nargs='+', type=ReadFile.from_path, help='Reads in fastq(.gz) format')
#     opts = assembly_parser.add_argument_group(bold('Output options'), "")
#     output_opts(opts)
#     opts = assembly_parser.add_argument_group(bold('Alignment options'), "")
#     alignmnent_opts(opts)
#     opts = assembly_parser.add_argument_group(bold('Database options'), "")
#     db_opts(opts)
#     opts.add_argument('--is-seqs', type=check_file, metavar='',
#                       help='Fasta file of IS element sequences to include in the database (default: None)')
#     opts = assembly_parser.add_argument_group(bold('Other options'), "")
#     other_opts(opts)


def extract_subparser(subparsers):
    extract_parser = subparsers.add_parser(
        'extract', description=get_logo('Extract entries from a Kaptive database'),
        epilog=f'kaptive extract v{__version__}', add_help=False, formatter_class=argparse.RawTextHelpFormatter,
        help='Extract entries from a Kaptive database', usage="kaptive extract <db> <format> [options]")
    opts = extract_parser.add_argument_group(bold('Inputs'), "\n - Note: combine with --filter to select loci")
    opts.add_argument('db', help='Kaptive database path or keyword', type=get_database)
    opts.add_argument('format', choices=['loci', 'genes', 'proteins', 'gbk', 'gff', 'ids'], metavar='format',
                      help='Format to extract database\n - loci: Loci (fasta nucleotide)\n'
                           ' - genes: Genes (fasta nucleotide)\n - proteins: Proteins (fasta amino acid)\n'
                           ' - gbk: Genbank format\n - gff: GFF in NCBI format\n - ids: List of Locus IDs')
    opts = extract_parser.add_argument_group(bold('Output options'), "")
    opts.add_argument('-o', '--out', metavar='', default=sys.stdout, type=Path, help='Output file (default: stdout)')
    opts = extract_parser.add_argument_group(bold('Database options'), "")
    db_opts(opts)
    opts = extract_parser.add_argument_group(bold('Other options'), "")
    other_opts(opts)


def convert_subparser(subparsers):
    convert_parser = subparsers.add_parser(
        'convert', description=get_logo('Convert Kaptive results into different formats'),
        epilog=f'kaptive convert v{__version__}', add_help=False, formatter_class=argparse.RawTextHelpFormatter,
        help='Convert Kaptive results into different formats',
        usage="kaptive convert <result> [<result> ...] [options]")
    opts = convert_parser.add_argument_group(
        bold('Inputs'),
        "\n - Note: If you used --is-seqs during the run, make sure to provide the same fasta file here")
    opts.add_argument('db', help='Kaptive database in genbank format', type=get_database)
    opts.add_argument('json', help='Kaptive result files', type=check_file, nargs='+')

    opts = convert_parser.add_argument_group(bold('Filter options'),
                                             "\n - Note: filters take precedence in descending order")
    opts.add_argument('-r', '--regex', metavar='', type=re.compile,
                      help='Regex to filter the string interpretation of the result (default: All)')
    opts.add_argument('-l', '--loci', metavar='', nargs='+',
                      help='Space-separated list to filter locus names (default: All)')
    opts.add_argument('-s', '--samples', metavar='', nargs='+',
                      help='Space-separated list to filter sample names (default: All)')

    opts = convert_parser.add_argument_group(bold('Output options'), "")
    # opts.add_argument('-o', '--out', metavar='', default=sys.stdout, type=argparse.FileType('wt'),
    #                   help='Output file (default: stdout)')
    opts.add_argument('-f', '--format', metavar='', default='json',
                      choices=['json', 'tsv', 'locus', 'genes', 'proteins'],
                      help='Output format (default: %(default)s)\n - json: JSON format\n - tsv: Tab-separated values\n'
                           ' - locus: Locus nucleotide sequence in fasta format\n'
                           ' - proteins: Proteins in fasta format\n - genes: Genes in fasta format')

    opts = convert_parser.add_argument_group(bold('Database options'), "")
    db_opts(opts)
    opts = convert_parser.add_argument_group(bold('Other options'), "")
    other_opts(opts)


def db_opts(opts: argparse.ArgumentParser):
    opts.add_argument('--locus-regex', type=re.compile, metavar='',
                      help='Pattern to match locus names in db source note, (default: %(default)s)')
    opts.add_argument('--type-regex', type=re.compile, metavar='',
                      help='Pattern to match locus types in db source note, (default: %(default)s)')
    opts.add_argument('--filter', type=re.compile, metavar='',
                      help='Pattern to select loci to include in the database (default: All)')


def alignment_opts(opts: argparse.ArgumentParser):
    # opts.add_argument('-@', '--mp', const=8, nargs='?', type=int, metavar='#',
    #                   help="Process multiple samples in parallel, optionally pass max workers (default: %(const)s)")
    opts.add_argument('-t', '--threads', type=check_cpus, default=check_cpus, metavar='',
                      help="Number of threads for minimap2 (default: %(default)s)")
    opts.add_argument('--args', metavar='', default='',
                      help='Additional arguments for minimap2 (default: %(default)s)')
    opts.add_argument('--preset', help='Preset for minimap2 (default: None)', metavar='',
                      choices=['map-pb', 'map-ont', 'map-hifi', 'ava-pb', 'ava-ont', 'asm5', 'asm10', 'asm20',
                               'splice', 'splice:hq', 'sr'])


def output_opts(opts: argparse.ArgumentParser):
    opts.add_argument('-o', '--out', metavar='', default=sys.stdout, type=argparse.FileType('at'),
                      help='Output file (default: stdout)')
    opts.add_argument('--fasta', metavar='path', nargs='?', default=None, const='.', type=check_dir,
                      help='Output locus sequence to "{input}_kaptive_results.fna"'
                           'Optionally pass output directory (default: current directory)')
    opts.add_argument('--json', metavar='prefix', nargs='?', default=None, const='kaptive_results.json',
                      type=argparse.FileType('at'),
                      help='Output results to json, optionally pass file name (default: %(const)s)')
    opts.add_argument('--figures', metavar='path', nargs='?', default=None, const='.', type=check_dir,
                      help='Output locus figures to "{input}_kaptive_results.png"'
                           'Optionally pass output directory (default: current directory)')
    opts.add_argument('--no-header', action='store_true', help='Do not print header line')
    opts.add_argument('--debug', action='store_true', help='Append debug columns to table output')


def other_opts(opts: argparse.ArgumentParser):
    opts.add_argument('-V', '--verbose', action='store_true', help='Print debug messages to stderr')
    opts.add_argument('-v', '--version', help='Show version number and exit', metavar='')
    opts.add_argument('-h', '--help', help='Show this help message and exit', metavar='')


def write_headers(args):
    """
    Write headers to output file if not already written
    """
    if args.out.name != '<stdout>' and args.out.tell() != 0:  # If file is path and not already written to
        args.no_header = True  # Headers already written, useful for running on HPC
    if not args.no_header:
        if args.subparser_name == 'assembly':
            args.out.write('\t'.join(_ASSEMBLY_HEADERS + _ASSEMBLY_EXTRA_HEADERS if args.debug else _ASSEMBLY_HEADERS) + '\n')
        # elif mode == 'reads':
        #     return _READS_HEADERS + _READS_EXTRA_HEADERS if extra_headers else _READS_HEADERS


# Main -----------------------------------------------------------------------------------------------------------------
def main():
    check_python_version()
    args = parse_args(sys.argv[1:])

    # args = parse_args([
    #     'assembly',
    #     # 'ab_k',
    #     'kpsc_k',
    #     # '/Users/tom/MyDrive/PostDoc/kaptive_project/test_data/abau/ncbi_subsampled/assemblies/SAMN27010226_hybrid.fasta',
    #     # '/Users/tom/MyDrive/PostDoc/kaptive_project/test_data/klebs/is_kooka/assemblies/KP_NORM_BLD_111588.fna.gz',
    #     # '/Users/tom/MyDrive/PostDoc/kaptive_project/test_data/klebs/KLEBGAP_hybrid/assemblies/NK_H14_058_10.fasta',
    #     '/Users/tom/MyDrive/PostDoc/serology_project/WITS_VIDA/assemblies/SAAA00136.fasta',
    #     '--no-header', '-V', '-t', '8'
    # ])

    if args.subparser_name == 'assembly':
        check_programs(['minimap2'], verbose=args.verbose)
        args.db = Database.from_genbank(  # Load database in memory, we don't need to load the full sequences (False)
            args.db, None, args.filter, False, locus_regex=args.locus_regex, type_regex=args.type_regex)
        if args.gene_threshold:
            args.db.gene_threshold = args.gene_threshold
        write_headers(args)
        from kaptive.assembly import typing_pipeline
        [typing_pipeline(assembly, args) for assembly in args.input]

        # elif args.subparser_name == 'reads':
        #     from kaptive.reads import type_reads
        #     temp_index = Path('kaptive_genes.mmi')  # Will replace with NamedTemporaryFile
        #     if not temp_index.is_file():
        #         log(f"Creating minimap2 index {temp_index}", args.verbose)
        #         with Popen(f"minimap2 -d {temp_index} -t {args.threads} -".split(), stdin=PIPE, stderr=DEVNULL) as proc:
        #             proc.communicate(db.as_gene_fasta().encode())
        #
        #     for name, reads in groupby(args.reads, lambda x: x.name):   # Group the args.reads by name
        #         reads = list(reads)  # Get the reads for the current sample
        #         type_reads(reads, db, temp_index, args)

    # TODO: Implement multiprocessing to process multiple samples in parallel
    # In the current implementation, typing_pipeline is the same speed with concurrent.futures.ThreadPoolExecutor
    # concurrent.futures.ProcessPoolExecutor is faster but it tries to write to the same file at the same time
    # which causes the output json and table to be malformed.

    elif args.subparser_name == 'extract':
        from kaptive.database import extract
        extract(args)

    elif args.subparser_name == 'convert':
        from kaptive.typing import parse_results
        args.db = Database.from_genbank(  # Load database in memory, we don't need to load the full sequences (False)
            args.db, args.is_seqs,  args.filter, False, locus_regex=args.locus_regex, type_regex=args.type_regex)
        for result_file in args.json:
            for result in parse_results(result_file, args.db, args.regex, args.samples, args.loci):
                if args.format == 'json':
                    sys.stdout.write(result.as_json())
                elif args.format == 'tsv':
                    sys.stdout.write(result.as_table())
                elif args.format == 'locus':
                    sys.stdout.write(result.as_fasta())
                elif args.format == 'genes':
                    sys.stdout.write(result.as_gene_fasta())
                elif args.format == 'proteins':
                    sys.stdout.write(result.as_protein_fasta())

