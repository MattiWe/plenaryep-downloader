import click
from pathlib import Path
from datetime import datetime
import json

from tqdm.auto import tqdm
from wasabi import msg as logger
from plenaryep_downloader.meps import ApiMepScaper
import plenaryep_downloader.reports as ep
from plenaryep_downloader.extraction import download_sources, Extractor

data = (Path(__file__).parents[2] / "data").absolute()
generated = (Path(__file__).parents[2] / "generated").absolute()
generated.mkdir(exist_ok=True)


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
def regenerate_meps(min_term: int, existing: str, verbose: bool):
    """ Re-generate the MEP metadata files to bring them up-to-date. """
    output = data / "mep-metadata.json"

    mep_scraper = ApiMepScaper(verbose)
    meps = mep_scraper(min_term, existing)
    
    json.dump(meps, open(output, "w"), ensure_ascii=False, indent=4)
    logger.info(f"Saved mep metadata to: {output.absolute()}")


@click.option('--existing',
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help='Path to an existing dataset - if set, only newer proceedings than whats in the existing dataset will be processed.')
@click.option('--no-filter', is_flag=True, default=False,
              help="When set, does not remove procedural and vote speeches.")
@click.option('--overwrite', is_flag=True, default=False,
              help="When set, overwrites the dataset given in `existing`")
@click.option('-v', '--verbose', is_flag=True, default=False)
@cli.command()
def corpus(existing: str | None, no_filter: bool, overwrite: bool, verbose: bool):
    newest = None
    dataset = []
    if existing:
        # dataset loader returns a sorted list of reports, the last one should be the newest
        dataset = ep.load(existing)
        newest = dataset[-1].date

    download_sources(newest=newest, verbose=verbose)

    # extractor = Extractor(verbose)
    # new_reports = [_ for _ in extractor(generated / "sources", newest=newest)]
    # logger.info(f"extracted {len(new_reports)} reports", show=verbose)
    # if verbose:
    #     extractor.eval()

    # dataset.extend(new_reports)

    # # write output with new name, leave the old one unchanged
    # now = datetime.now()
    # if overwrite and existing:
    #     output = Path(existing)
    # else:
    #     output = generated / f"plenaryep_{now.strftime('%y-%m-%d')}.jsonl"
    # ep.save(dataset, output, no_filter)
    # logger.info(f"Saved corpus to: {output.absolute()}")


@click.option('-v', '--verbose', is_flag=True, default=False)
@click.argument('input', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@cli.command()
def translate(
    input: str,
    verbose: bool
    ):
    dataset = ep.load(input)
    
    # add new translations to a temp dir as a log, only add to the corpus when everything is ready
    
    ep.save(dataset, input)
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
    # logger = logging.getLogger(__name__)
    cli()
