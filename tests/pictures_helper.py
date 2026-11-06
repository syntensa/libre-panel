"""Stand-ins for the pictures' process (tests/test_pictures.py): one that ends at once, one that
takes a job and ends before it is ready, and one that is ready and ends while it draws."""

import os


def ends_at_once(conn):
    os._exit(3)


def ends_before_it_is_ready(conn):
    while conn.recv()[0] != "job":
        pass
    os._exit(4)


def ends_while_drawing(conn):
    conn.send(("ready",))
    while conn.recv()[0] != "job":
        pass
    os._exit(5)
