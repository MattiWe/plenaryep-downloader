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

This will create a file `data/mep-metadata.json`. 


## 2. Proceedings

The proceedings are crawled and extracted from `https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iPlCre_Sit/`. 

    ```
    python3 plenaryep.py corpus
    ```


## 3. Translations

Translations are only added on demand to an existing corpus. The script checks for a missing 'translation' field and saves checkpoints, so interruptions are possible. By default, we use the `translators` package to access Google Translate, which does not need an account or billing, but takes longer due to access limits. 

    ```
    python3 plenaryep.py translate
    ```


## 4. CAP Topic Classification

CAP Topic are only added on demand to an existing corpus. The script checks on a per-proceedings level to skip existing ones. This script uses the `openai` package to access an LLM via API, i.e. a separate vLLM. 

    ```
    python3 plenaryep.py cap
    ```