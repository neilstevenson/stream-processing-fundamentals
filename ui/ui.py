import logging
import os
import sys
import threading

import hazelcast
from dash import Dash, html, dcc
import plotly.express as px
import pandas as pd
from hazelcast import HazelcastClient
from hazelcast.config import ReconnectMode
from hazelcast.proxy.base import EntryEvent
from hazelcast.proxy.map import BlockingMap
from hazelcast.serialization.api import Portable, PortableWriter, PortableReader


# the following environment variables are required
#
# HZ_SERVERS
# HZ_CLUSTER_NAME
#


class MachineStatusEvent(Portable):
    ID = 2

    def __init__(self):
        self.serial_num = ""
        self.event_time = 0
        self.bit_rpm = 0
        self.bit_temp = 0
        self.bit_position_x = 0
        self.bit_position_y = 0
        self.bit_position_z = 0

    def write_portable(self, writer: PortableWriter) -> None:
        writer.write_string("serialNum", self.serial_num)
        writer.write_long("eventTime", self.event_time)
        writer.write_int("bitRPM", self.bit_rpm)
        writer.write_short("bitTemp", self.bit_temp)
        writer.write_int("bitPositionX", self.bit_position_x)
        writer.write_int("bitPositionY", self.bit_position_y)
        writer.write_int("bitPositionZ", self.bit_position_z)

    def read_portable(self, reader: PortableReader) -> None:
        self.serial_num = reader.read_string("serialNum")
        self.event_time = reader.read_long("eventTime")
        self.bit_rpm = reader.read_int("bitRPM")
        self.bit_temp = reader.read_short("bitTemp")
        self.bit_position_x = reader.read_int("bitPositionX")
        self.bit_position_y = reader.read_int("bitPositionY")
        self.bit_position_z = reader.read_int("bitPositionZ")

    def get_factory_id(self) -> int:
        return 1

    def get_class_id(self) -> int:
        return MachineStatusEvent.ID


portable_factory = {MachineStatusEvent.ID: MachineStatusEvent}


def get_required_env(name: str) -> str:
    if name not in os.environ:
        sys.exit(f'Please provide the "{name} environment variable."')
    else:
        return os.environ[name]


# returns a map listener that listens for a certain value using closure
def wait_map_listener_fun(expected_val: str, done: threading.Event):
    def inner_func(e: EntryEvent):
        if e.value == expected_val:
            print("event with expected value observed", flush=True)
            done.set()

    return inner_func


def logging_entry_listener(entry: EntryEvent):
    print(f'GOT {entry.key}: {entry.value.bit_temp}', flush=True)


def wait_for(imap: BlockingMap, expected_key: str, expected_val: str, timeout: float) -> bool:
    done = threading.Event()
    imap.add_entry_listener(
        include_value=True,
        key=expected_key,
        added_func=wait_map_listener_fun(expected_val, done),
        updated_func=wait_map_listener_fun(expected_val, done)
    )

    curr_val = imap.get(expected_key)
    if curr_val is not None and curr_val == expected_val:
        return True

    return done.wait(timeout)


app = Dash(__name__)

now = pd.Timestamp.now()

d = {
    "abc": pd.Series([101, 102, 103, 102, 101, 104, 108, 111], index=[now + pd.Timedelta(seconds=s) for s in range(8)]),
    "def": pd.Series([101, 97, 104, 96], index=[now + pd.Timedelta(seconds=s) for s in range(0, 8, 2)])
}
pd.options.plotting.backend = "plotly"
df = pd.DataFrame(d)
df["def"].interpolate(inplace=True)
print(df)

fig = df.plot(template='plotly_dark')

app.layout = html.Div(children=[
    html.H1(children='Hello Dash'),

    html.Div(children='''
        Dash: A web application framework for your data.
    '''),

    dcc.Graph(
        id='example-graph',
        figure=fig
    )
])

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    hz_cluster_name = get_required_env('HZ_CLUSTER_NAME')
    hz_servers = get_required_env('HZ_SERVERS').split(',')
    hz = HazelcastClient(
        cluster_name=hz_cluster_name,
        cluster_members=hz_servers,
        async_start=False,
        reconnect_mode=ReconnectMode.ON,
        portable_factories={
            1: portable_factory
        }
    )
    print('Connected to Hazelcast', flush=True)
    machine_controls_map = hz.get_map('machine_controls').blocking()
    event_map = hz.get_map('machine_events').blocking()
    system_activities_map = hz.get_map('system_activities').blocking()
    wait_for(system_activities_map, 'LOADER_STATUS', 'FINISHED', 3 * 60 * 1000)
    print("The loader has finished, proceeding", flush=True)

    selected_serial_nums = hz.sql.execute(
        """SELECT serialNum FROM machine_profiles WHERE
           location = 'Los Angeles' AND
           block = 'A' """
    ).result()

    sn_list = "','".join([r["serialNum"] for r in selected_serial_nums])
    query = f"serialNum in ('{sn_list}')"
    print(f'adding entry listener WHERE {query}', flush=True)
    event_map.add_entry_listener(
        include_value=True,
        predicate=hazelcast.predicate.sql(query),
        added_func=logging_entry_listener,
        updated_func=logging_entry_listener)

    print("Listener added", flush=True)

    app.run_server(debug=True)
