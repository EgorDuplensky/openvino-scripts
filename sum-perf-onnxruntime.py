#!/usr/bin/env python3
import json
import pandas as pd
import argparse

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', required=False, help="print as 'csv' data", action='store_true')
    parser.add_argument('--group_by', '-g', choices=['name', 'args.op_name'], default=['args.op_name'], required=False, help="print data using format", nargs='+')
    parser.add_argument('onnxruntime_profile_file')
    return parser.parse_args();

def get_dataframe(path):
    with open(path) as f:
        d = json.load(f)
        return pd.json_normalize(d)

def aggregate(df, group_by):
    df = df[['name', 'dur', 'args.op_name', 'args.provider']]
    # args.provider must be present
    defined_kernel_time=df[df['args.provider'].notnull()]
    # group by all the inferences first
    aggregated = defined_kernel_time.groupby(['name', 'args.op_name'])['dur'].mean().reset_index()
    # group by requested group
    aggregated = aggregated.groupby(group_by, as_index=False)['dur'].agg(['count','sum'])
    # sort by duration
    result = aggregated.sort_values(by=['sum'], ascending=False)
    result = result.round({'sum': 3})
    # microseconds to milliseconds
    result.loc[:,'sum'] /= 1000
    # add percentage
    result['%'] = (result['sum'] / result['sum'].sum()) * 100
    # add total
    result.loc['Total'] = result.sum(numeric_only=True)
    result.at['Total', 'args.op_name'] = 'Total'
    # round percentage
    result = result.round({'%': 2})
    # ensure count as int (no trailing .0)
    result['count'] = result['count'].astype('int')
    # rename columns
    result = result.rename(columns={"args.op_name": "Node type", "count": "Count", "sum":"Sum (ms)"})
    return result

if __name__ == "__main__":
    args = parse_args()
    df = get_dataframe(args.onnxruntime_profile_file);
    df = aggregate(df, args.group_by)

    if args.csv:
        print(df.to_csv(index=False))
    else:
        print(df.to_string(index=False))
