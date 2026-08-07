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

logger = logging.getLogger(__name__)
logger.setLevel(level=logging.ERROR)


@click.group()
def cli():
    pass


@click.option('-v', '--verbose', is_flag=True, default=False)
@cli.command()
def regenerate_meps(verbose: bool):
    """ Re-generate the MEP metadata files to bring them up-to-date. """
    if verbose:
        logger.setLevel(level=logging.DEBUG)

    output = data / "mep-metadata.json"
    ches_map = json.load(open(data / "ches-map.json"))
    group_map = json.load(open(data / "group-map.json"))

    meps, ches_misses = get_meps(ches_map,
                                 group_map, 
                                 verbose)
    
    json.dump(meps, open(output, "w"), ensure_ascii=False, indent=4)
    logger.warning(f"Saved to: `data/mep-metadata.json`")

    json.dump(ches_misses, 
              open(generated / "ches-misses.json", 'w'), 
              ensure_ascii=False, 
              indent=4)
    logger.warning(f"XXX parties could not be mapped to a CHES party.\nThese party names can be added to `data/ches-map.json` where appropriate, then re-generate the MEP list.\n\nFind the missed parties here: `generated/ches-misses.json`")


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
    logger.error("INFO")
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
    cli()
