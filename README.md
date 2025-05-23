# Vivarium *E. coli*

![vivarium](doc/_static/ecoli_master_topology.png)

## Background

Vivarium *E. coli* (vEcoli) is a port of the Covert Lab's 
[E. coli Whole Cell Model](https://github.com/CovertLab/wcEcoli) (wcEcoli)
to the [Vivarium framework](https://github.com/vivarium-collective/vivarium-core).
Its main benefits over the original model are:

1. **Modular processes:** easily add/remove processes that interact with
    existing or new simulation state
2. **Unified configuration:** all configuration happens through JSON files,
    making it easy to run simulations/analyses with different options
3. **Parquet output:** simulation output is in a widely-supported columnar
    file format that enables fast, larger-than-RAM analytics with DuckDB
4. **Google Cloud support:** workflows too large to run on a local machine
    can be easily run on Google Cloud

As in wcEcoli, [raw experimental data](reconstruction/ecoli/flat) is first processed
by the parameter calculator or [ParCa](reconstruction/ecoli/fit_sim_data_1.py) to calculate 
model parameters (e.g. transcription probabilities). These parameters are used to configure
[processes](ecoli/processes) that are linked together into a
[complete simulation](ecoli/experiments/ecoli_master_sim.py).

## Setup

> **Note:** The following instructions assume a local Linux or MacOS system. Windows users can
> attempt to follow the same steps after setting up 
> [Windows Subsystem for Linux](https://learn.microsoft.com/en-us/windows/wsl/install). Refer to the following pages for non-local setups:
> [Sherlock](https://covertlab.github.io/vEcoli/hpc.html#sherlock),
> [other HPC cluster](https://covertlab.github.io/vEcoli/hpc.html#other-clusters),
> [Google Cloud](https://covertlab.github.io/vEcoli/gcloud.html).

### Prerequisites

If your system has git, curl (or wget), and a C compiler
(e.g. clang, gcc), proceed to the next section.

On Ubuntu/Debian, apt can be used to install all three prerequisites:

    sudo -s eval 'apt update && apt install git curl clang'

On MacOS, curl is preinstalled and git and clang come with the Xcode Command Line Tools:

    xcode-select --install

### Installation

Clone the repository:

    git clone https://github.com/CovertLab/vEcoli.git

> **Tip:** You can specify a directory to clone into after the
> URL of the repository. Otherwise, the above command will clone into
> a new directory called `vEcoli` in your current directory.

[Follow these instructions](https://docs.astral.sh/uv/getting-started/installation/)
to install `uv`, our Python package and project manager of choice. Once finished,
close and reopen your terminal before continuing.

Navigate into the cloned repository and use `uv` to install the model:

    cd vEcoli
    # Install base and dev dependencies (see pyproject.toml)
    uv sync --frozen --extra dev
    # Install pre-commit hook that runs ruff linter before every commit
    uv run pre-commit install

Install `nextflow` [following these instructions](https://www.nextflow.io/docs/latest/install.html).
If your system has `wget` but not `curl`, replace `curl` in the commands
with `wget -qO-`. If you choose to install Java with SDKMAN!, after
the Java installation finishes, close and reopen your terminal before
continuing with the `nextflow` installation steps.

> **Tip:** If any step in the `nextflow` installation fails,
> try rerunning a few times to see if that fixes the issue.

Add the following to your `~/.bashrc` and/or `~/.zshrc`, replacing `abspath` with
the absolute path to your cloned repository (output of `pwd` from top level of
cloned repo):

    # Specify project and use absolute paths so that `uvenv` correctly loads
    # the vEcoli virtual environment no matter what directory it is run in
    alias uvenv='uv run --env-file abspath/.env --project abspath'

> **Tip:** Run `echo $0` to determine what shell you are using and the
> appropriate file to add the above line to.

Close and reopen your terminal.

For integration with IDEs, select the newly created virtual environment
located at `.venv` inside your cloned repository (e.g.
[for PyCharm](https://www.jetbrains.com/help/pycharm/uv.html)).

## Test Installation

To test your installation, from the top-level of the cloned repository, invoke:

    uvenv runscripts/workflow.py --config ecoli/composites/ecoli_configs/test_installation.json

> **Note:** Use `uvenv` to run scripts instead of `python`. To start
> a Python shell, use `uvenv python` or `uvenv ipython`.

This will run the following basic simulation workflow:

1. Run the [parameter calculator](runscripts/parca.py) to generate simulation data.
2. Run the [simulation](ecoli/experiments/ecoli_master_sim.py)
    for a single generation, saving output in `out` folder.
3. [Analyze simulation output](runscripts/analysis.py) by creating a
    [mass fraction plot](ecoli/analysis/single/mass_fraction_summary.py).

## Next Steps
Review the online [user guide](https://covertlab.github.io/vEcoli/) to learn how
to configure and run your own simulation workflows.

If you encounter a problem that you cannot solve after searching the user guide
(also linked in the repository sidebar), feel free to create a GitHub issue, and we will
get back to you as soon as we can.
