# plenaryep-downloader

## Setup

This project uses uv. Setup the dependencies with 

    ```
    uv sync
    source .venv/bin/activate
    ```

All commands are run with a command line tool. Call this script to see the help:

    ```
    python3 src/plenaryep.py
    ```

## 1. MEP Metadata

Building or updating the corpus directly uses MEP metadata from an external source, so this metadata file should be build first:

    ```
    python3 plenaryep.py regenerate-meps
    ```

This will create a file `data/mep-metadata.json`. Check `--help` for more options.


## 2. Proceedings

The proceedings are crawled and extracted from `https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iPlCre_Sit/`. 

    ```
    python3 plenaryep.py corpus \
      --existing /path/to/plenaryep.jsonl
    ```

This will download the sources, extract the debates, and save the resulting dataset. When providing an existing dataset, only debates newer than the ones within are downloaded. The option `--no-filter` disables filtering of votes and procedural speeches. Check `--help` for more options.

## 3. Translations

Translations are only added on demand to an existing corpus. The script checks for a missing 'translation' field and saves checkpoints, so interruptions are possible. By default, we use the `translators` package to access Google Translate, which does not need an account or billing, but takes longer due to access limits. 

    ```
    python3 plenaryep.py translate /path/to/plenaryep.jsonl
    ```

This will translate new speeches using Google translate via `translators`. Only translates non-English speeches without a s set *translation* metadate. Check `--help` for more options.

## 4. CAP Topic Classification

CAP Topic are only added on demand to an existing corpus. The script checks on a per-proceedings level to skip existing ones. This script uses the `openai` package to access an LLM via API, i.e. a separate vLLM. 

    ```
    python3 plenaryep.py cap /path/to/plenaryep.jsonl --server https://openai/compatible/endpoint
    ```

This will determine the cap topics. It uses sentence-transformers locally for ranking and requests the api for classification. Reports where at least one speech was already classified will be skipped. Check `--help` for more options.