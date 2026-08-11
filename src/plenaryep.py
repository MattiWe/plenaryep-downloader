import click
from pathlib import Path
from statistics import mean
from tqdm.auto import tqdm
import logging
from datetime import datetime
import json

from meps import get_meps

data = (Path(__file__).parents[1] / "data").absolute()
generated = (Path(__file__).parents[1] / "generated").absolute()
generated.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)


@click.group()
def cli():
    pass


@click.option('-t', '--min-term', 
              help='Earliest term from where meps should be included.',
              type=click.IntRange(0, 10), default=4)
@click.option('--existing',
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help='Path to a mep tmp file - set if the re-generation was interrrupted to continue from the latest state.')
@click.option('-v', '--verbose', 
              is_flag=True, default=False)
@cli.command()
def regenerate_meps(min_term: int, 
                    existing: str,
                    verbose: bool):
    """ Re-generate the MEP metadata files to bring them up-to-date. """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    output = data / "mep-metadata.json"

    meps = get_meps(min_term, existing, verbose)
    
    json.dump(meps, open(output, "w"), ensure_ascii=False, indent=4)
    logger.info(f"Saved mep metadata to: {output.absolute()}")


@click.option('--existing',
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help='Path to an existing dataset - if set, only newer proceedings than whats in the existing dataset will be processed.')
@click.option('-t', '--translate', is_flag=True, default=False)
@click.option('-v', '--verbose', is_flag=True, default=False)
@cli.command()
def corpus(
    existing: str | None,
    verbose: bool
    ):
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    logger.info("info")
    logger.error(existing)
    logger.debug("DEBUG")
    now = datetime.now()
    # output
    output = generated / f"plenaryep_{now.strftime('%y-%m-%d')}.jsonl"

    # TODO add MEP and CHES data
    meps = data / "mep-metadata.json"

    # write with new name, leave the old one unchanged


@cli.command()
def translate(
    input: str | None,
    verbose: bool
    ):    
    # add new translations to a temp dir as a log, only add to the corpus when everything is ready
    pass


@cli.command()
def cap(
    input: str | None,
    verbose: bool
    ):    
    # add new cap classification to a temp dir as a log, only add to the corpus when everything is ready
    cap = data / "mep-metadata.json"
    thresholds = data / "cap-thresholds.json"
    

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    cli()
