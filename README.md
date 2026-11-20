# Local AI Test Case Generator

A Python desktop app for drafting QA test cases from requirements. The PyQt interface accepts pasted text or TXT, PDF, and DOCX files. It uses LangChain and an Ollama model to generate cases for selected test types and exports parsed cases to CSV.

## Run locally

1. Install Python and Ollama. Start Ollama, then run `ollama pull llama3.2:3b`. The model download is about 2 GB. Allow additional disk space for the Python environment and dependencies.
2. Create a virtual environment with `python -m venv .venv` and activate it.
3. Run `python -m pip install -r requirements.txt`.
4. Run `python TestCaseGenapp.py`.
5. Select an installed model, enter or import a requirement, and generate cases. Review the output before exporting it.

The default Ollama endpoint is `http://localhost:11434`. Model availability and output quality depend on your setup. Generated test cases are drafts and need review for coverage and correct expected results.

## Verified local run

On November 20, 2026, the Windows desktop app generated test cases using Ollama and `llama3.2:3b`. It exported 27 of 30 requested cases: 7 Positive, 7 Boundary, 6 Functional, and 7 Smoke. All 27 CSV rows had unique IDs and populated test steps and expected outputs. The export check verified the file structure. Coverage and correctness still require tester review.

## Example requirement

A synthetic insurance portal must create one claim when a user submits a complete form. Retrying the same submission after a response timeout must return the original claim ID and create no duplicate. A missing loss date must prevent claim creation.

Check the generated cases for successful creation, validation of the required date, and retry behavior. For the retry case, the expected result should assert both the original claim ID and a single saved claim.


## Build a Windows executable

From the repository root in PowerShell, install the dependencies and build with PyInstaller:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --onefile --noconsole --name TestCaseGenapp .\TestCaseGenapp.py
```

The executable is created at `dist/TestCaseGenapp.exe`. Ollama must be installed and running separately, with a model already downloaded.

The standard build uses PyInstaller's built-in hooks. If you add custom hooks to a `hooks` directory in this repository, use `--additional-hooks-dir .\hooks` in the build command. For startup troubleshooting, build once without `--noconsole` to see error output.
