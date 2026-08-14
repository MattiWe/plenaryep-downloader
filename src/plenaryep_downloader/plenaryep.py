import click
from pathlib import Path
from datetime import datetime
import json

from tqdm.auto import tqdm
from wasabi import msg as logger
from plenaryep_downloader.meps import ApiMepScaper
from plenaryep_downloader.translation import Translator
import plenaryep_downloader.reports as ep
from plenaryep_downloader.extraction import download_sources, Extractor
from plenaryep_downloader.cap import CapClassifier

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
              help='Path to a mep tmp file - set if the re-generation was interrrupted, continue from the latest state.')
@click.option('-v', '--verbose', 
              is_flag=True, default=False)
@cli.command()
def regenerate_meps(min_term: int, existing: str, verbose: bool):
    """ Re-generate the MEP metadata files to bring them up-to-date. 
    
    This may take a while. On Exception, it saves a temp-file to `generated/tmp-meps.json`. This can be given as `existing` to continue from there. 
    """
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
@click.option('--skip-download', is_flag=True, default=False,
              help="When set, skip downloading new source files and only use the cache")
@click.option('-v', '--verbose', is_flag=True, default=False)
@cli.command()
def corpus(existing: str | None, 
           no_filter: bool, 
           overwrite: bool, 
           skip_download: bool,
           verbose: bool):
    """Download the source .xml files of the Verbatim Records of Proceedings from the European Parliament. Extract debates and filter out non-debate content. 

    Without parameters, this will download everything since 1996. Provide an `existing` dataset, and the crawler will only download reports that are newer. Downloaded source are kept as a cache in `generated/sources` and are not downloaded again. 
    """
    newest = None
    dataset = []
    if existing:
        # dataset loader returns a sorted list of reports, the last one should be the newest
        dataset = ep.load(existing)
        newest = dataset[-1].date

    if not skip_download:
        download_sources(newest=newest, verbose=verbose)

    extractor = Extractor(verbose)
    new_reports = [_ for _ in extractor(generated / "sources", newest=newest)]
    logger.info(f"extracted {len(new_reports)} reports", show=verbose)
    if verbose:
        extractor.eval()

    dataset.extend(new_reports)

    # write output with new name, leave the old one unchanged
    now = datetime.now()
    if overwrite and existing:
        output = Path(existing)
    else:
        output = generated / f"plenaryep_{now.strftime('%y-%m-%d')}.jsonl"
    ep.save(dataset, output, no_filter)
    logger.info(f"Saved corpus to: {output.absolute()}")


@click.option('--detect-lang', is_flag=True, default=False,
              help="When set, use language detection as an extra layer of validation for the translation.")
@click.option('--accelerate', is_flag=True, default=False,
              help="When set, use the tranlsation libraries accelecation pre-routine.")
@click.option('-v', '--verbose', is_flag=True, default=False)
@click.argument('input', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@cli.command()
def translate(input: str,
              detect_lang: bool,
              accelerate: bool,
              verbose: bool):
    dataset = ep.load(input)

    translator = Translator(verbose, detect_lang, accelerate)
    for report in tqdm(dataset, desc="reports", total=len(list(dataset))):
        translated_report = translator(report)
        if not translated_report:
            continue
        report = translated_report
        ep.save(dataset, input)


@click.option('-v', '--server', type=str,
              help="Server that responds to the openAI library (i.e. vllm).")
@click.option('-v', '--token', type=str, default="token-abc123",
              help="Outh token for the server")
@click.option('-v', '--api-checkpoint', type=str, default="Qwen/Qwen3-8B",
              help="Huggingface Checkpoint for the model used for the classification part")
@click.option('-v', '--verbose', is_flag=True, default=False)
@click.argument('input', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@cli.command()
def cap(input: str,
        server: str, 
        token: str,
        api_checkpoint: str,
        verbose: bool):    
    
    dataset = ep.load(input)
    clf = CapClassifier(verbose, server, token, api_checkpoint)
    for report in tqdm(dataset, desc="reports", total=len(list(dataset))):
        classified_report = clf(report) 
        if not classified_report:
            continue
        report = classified_report
        ep.save(dataset, input)


if __name__ == "__main__":
    cli()
