import sys
import json
import pandas as pd
from typing import Tuple, List
from analysis_smi import smi_analysis
RUN_TYPE = "post-git-test"

def main():
    if len(sys.argv) <= 1:
        exit("usage: analysis.py run OR analysis.py <hl-smi/nvidia-smi>")  
    elif sys.argv[1].__eq__("run"):
        run_analysis()
    elif sys.argv[1].__eq__("smi"):
        if len(sys.argv) > 2:
            smi_analysis(sys.argv[2])
        else:
            smi_analysis("all")
    else:
        exit("usage: analysis.py run OR analysis.py smi")

def run_analysis():
    
    t_info = []
    e_info = []
    
    ## These are the variables to change when targetting different files
    ## Sometimes these target a single run. Sometimes both lists have 3+ entries. Should work either way.
    skeleton = RUN_TYPE + "/128-duped-{}"
    # skeleton = "theta/128-{}-theta"
    formats = ["128", "256"]
    run_numbers = ["1", "2", "3"]

    total_times = dict.fromkeys(formats)
    for format in formats:
        trains, evals, facts = load_runs(skeleton.format(format), run_numbers)
        t_info.append(trains)
        e_info.append(evals)
        total_times[format] = facts

    # Hand off to the analysis functions so they can do their work.
    # We get back the dataframes in whatever state, but don't currently use them.
    train_runs = train_analysis(t_info, formats, run_numbers)
    eval_runs = eval_analysis(e_info, formats, run_numbers)
    outside_runs = outsides_analysis(total_times, formats, run_numbers)

    # Collect all the data present in each return
    collector = {key: {} for key in train_runs.keys()}
    for run in collector.keys():
        for k, v in train_runs[run].items():
            collector[run][k] = v
        for k, v in eval_runs[run].items():
            collector[run][k] = v
        for k, v in outside_runs[run].items():
            collector[run][k] = v
    
    # Get the data into a reasoanble form, then print out.
    json_safe = cat_tuples(collector, "habana duped")
    print(json.dumps(json_safe, indent=2))

def cat_tuples(data: dict, affix: str) -> dict:
    """
    Turns tuples in dict keys to json-acceptable (and human-legible) form.
    Arguments:
        data: The dict which to format.
        affix: A string which to add to joined tuples.
    Returns: the same dicts with tuple keys turned into strings.
    """
    dup = {}
    for key, value in data.items():
        dup_key = f"{affix} {key}"
        if type(key) is tuple:
            dup_key = f"{affix} {key[1]}: run {key[0]}"
        dup[dup_key] = value
    dup = dict(sorted(dup.items(), key=lambda v: v[0]))
    return dup


def train_analysis(train_data: List[pd.DataFrame], formats: List[str], run_names: List[str]) -> Tuple[pd.DataFrame, dict]:
    """
    The analysis function for the train values. Does most of the heavy lifting
    Arguments:
        train_data: A list of the train dataframes.
        formats: A list of strings of the different types of runs.
        run_names: A list of all the run names within formats.
    Returns: a dict of the important train-specific information.
    """
    
    # Create runs dict, index by tuple cause that's an easy way to seperate the data.
    runs = {(run, format): dict() for run in run_names for format in formats}
    t_info = merge_cols(train_data, [f"{kind}_{name}" for kind in ["time", "loss"] for name in run_names], formats)
    
    # Get all the first epoch timing data, as the data's loading in slowly at this point.
    delayed = lambda : f"time_{run}_{format}"
    for format in formats:
        runs[format] = {}
        runs[format]["first epoch"] = 0.0
        for run in run_names:
            runs[(run, format)]["first epoch train time"] = t_info.max().get(delayed())
            runs[format]["first epoch"] += runs[(run, format)]["first epoch train time"] / len(run_names)
    
    # Remove first epoch timing data as to not skew other metrics.
    t_info.drop(t_info.index[:1], inplace=True)

    # Get train epoch timing data, average, minimum, and maximum.
    for format in formats:
        runs[format]["average train time"] = 0.0
        for run in run_names:
            runs[(run, format)]["average train time"] = t_info.mean().get(delayed())
            runs[(run, format)]["max train time"] = t_info.loc[t_info[delayed()].idxmax()][delayed()]
            runs[(run, format)]["min train time"] = t_info.loc[t_info[delayed()].idxmin()][delayed()]
            runs[format]["average train time"] += runs[(run, format)]["average train time"] / len(run_names)

    if len(run_names) * len(formats) == 1:
        del runs[format]
    return runs


def eval_analysis(eval_data: List[pd.DataFrame], formats: dict, run_names: List[str]) -> Tuple[pd.DataFrame, dict]:
    """
    The analysis function for the eval values.
    Arguments:
        eval_data: A list of the eval dataframes.
        formats: A list of strings of the different types of runs.
        run_names: A list of all the run names within formats.
    Returns: a dict of the important eval-specific information.
    """

    # Create runs dict, index by tuple cause that's an easy way to seperate the data.
    runs = {(run, format): dict() for run in run_names for format in formats}
    
    # Merge the dataframes into a single one, using to_merges for their col names
    to_merges = [f"{kind}_{num}" for kind in ["time", "loss", "dsc"] for num in run_names]
    e_info = merge_cols(eval_data, to_merges, formats)
    
    # Also create this for averages.
    for format in formats:
        runs[format] = dict()

    # Drop the first epoch from the data, unimportant for dsc, messes with time.
    e_info.drop(e_info.index[:1], inplace=True)

    dsc_key = "max dsc"
    # Iterate through to get: maximum dsc, average eval time per epoch.
    for format in formats:
        runs[format]["average eval time"] = 0.0
        runs[format][dsc_key] = 0.0
        for run in run_names:
            runs[(run, format)][dsc_key] = e_info.loc[e_info[f"time_{run}_{format}"].idxmax()][f"time_{run}_{format}"]
            runs[(run, format)]["average eval time"] = e_info.mean().get(f"time_{run}_{format}")
            runs[format]["average eval time"] += runs[(run, format)]["average eval time"] / len(run_names)
            runs[format][dsc_key] += runs[(run, format)][dsc_key] / len(run_names)

    if len(run_names) * len(formats) == 1:
        del runs[format]
    return runs


def outsides_analysis(times_data: dict, formats: List[str], run_names: List[str]) -> dict:
    """
    The analysis function for the non-train non-eval values. Doesn't change much, can.
    Arguments:
        times_data: A dict of the outside data.
        formats: A list of strings of the different types of runs.
        run_names: A list of all the run names within formats.
    Returns: a dict of the important information outside of train and eval.
    """

    # Create runs dict, index by tuples, and copy the already-generated data into it.
    runs = {(run, format): dict() for run in run_names for format in formats}
    for format in formats:
        for run in run_names:
            runs[(run, format)] = times_data[format][run].copy()

    # For each type of run, average out all of their fields.
    for format in formats:
        runs[format] = {}
        for key in times_data[format][run].keys():
            avg = 0.0
            for name in run_names:
                avg += runs[(name, format)][key]
            runs[format][key] = avg / len(run_names)

    if len(run_names) * len(formats) == 1:
        del runs[format]
    return runs


def load_runs(source: str, run_names: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    The go-to method when there are multiple runs for the same model.
    Gets a group of specified csvs and combines their data into two dataframes and one dict.
    Arguments:
        source: A string for the source (shared) name between the models.
        run_names: A list of the identifying parts of the names
    Returns: a tuple with three entries, train data, eval data, and outside data
    """

    # Make for each type of data generated from the files.
    trains = []
    evals = []
    facts = []
    for name in run_names:
        train, eval, fact = load_blocks(f"csvs/{source}-{name}.csv")
        trains.append(train)
        evals.append(eval)
        facts.append(fact)

    # Transform out of lists into frames and dict, then return
    t_info = merge_cols(trains, ["time", "loss"], run_names)
    e_info = merge_cols(evals, ["time", "loss", "dsc"], run_names)
    return t_info, e_info, {run_names[i]: facts[i] for i in range(len(facts))}


def merge_cols(data: List[pd.DataFrame], columns: List[str], names: List[str]) -> pd.DataFrame:
    """
    A helper function, merges any number of dataframes together.
    Arguments:
        data: A list containing all the dataframes to merge.
        columns: A list of strings that specifies the important columns
        names: A list of strings containing the name of every run. Should match length to data.
    Returns: A single dataframe with merged data.
    """

    # Edge for length one. Still rename columns for outward consistency.
    if len(data) == 1:
        data[0].rename(columns = {f"{col}": f"{col}_{names[0]}" for col in columns}, inplace=True)
        return data[0]
    merger = data[0].copy()

    #TODO This renaming scheme only works for 3, and it should for 2. Who knows about 4+
    # Anyway, merge data, then fix all the conflicting names
    for entry in data[1:]:
        merger = merger.merge(entry, on="epoch")

    if len(names) < 3: 
        names.append(None)
    for col in columns:
        merger.rename(columns = {
            f"{col}_x": f"{col}_{names[0]}",
            f"{col}_y": f"{col}_{names[1]}",
            f"{col}": f"{col}_{names[2]}",
        }, inplace=True, errors='ignore')
    if None in names:
        names.remove(None)

    return merger


def load_blocks(filepath: str) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Loads all the data from csv format.
    Arguments:
        filepath: the file to load.
    Returns:
        A dataframe of all the training epochs.
        A dataframe of all the eval epochs.
        A dict of the values distinct from both of those.
    """
    data = pd.read_csv(filepath)

    # Neither train nor eval needs the type column as that's their key, and train doesn't need dsc.
    t_info = data[data["type"] == "train"].drop(columns=['type', 'dsc'])
    e_info = data[data["type"] == "eval"].drop(columns=['type'])

    # All the rest of the data is more specific and needs to be found directly
    loader_time = data.at[data[data["type"] == "loaders_init"].index[0], "time"]
    total_train = data.at[data[data["type"] == "total_train_time"].index[0], "time"]
    total_eval = data.at[data[data["type"] == "total_eval_time"].index[0], "time"]
    return t_info, e_info, {"loaders_init": loader_time, "total_train": total_train, "total_eval": total_eval}
    

if __name__ == '__main__':
    main()