#! -*- coding: utf-8 -*-
"""
Set of functions and objects to retreive and parse the output of MongoDB's
"db.system.profile.find()"
"""
import datetime
import pymongo
import re
_re_command_record = re.compile(
    ur'query (?P<db>[^.]+)\.\$cmd ntoreturn:(?P<ntoreturn>\d+) '
    ur'command: (?P<command>{.*}) (?P<options>.*)')
_re_query_record = re.compile(
    ur'query (?P<db>[^.]+)\.(?P<collection>[^ ]+) (?P<options1>.*)\n'
    ur'query: (?P<query>{.*}) (?P<options2>.*)'
)
_re_getmore_record = re.compile(
    ur'getmore (?P<db>[^.]+)\.(?P<collection>[^ ]+) '
    ur'(?P<options1>.*?) getMore: (?P<query>{.*})(?P<options2>.*)')
_re_marker_record = re.compile(
    ur'query (?P<db>[^.]+)\.phony_mongoprofile_marker.*\n'
    ur'query: { \$query: { text: "(?P<text>.*)" } }'
)
_re_insert_record = re.compile(
    ur'insert (?P<db>[^.]+)\.(?P<collection>[^ ]+)'
)
_re_update_record = re.compile(
    ur'update (?P<db>[^.]+)\.(?P<collection>[^ ]+)  query: (?P<query>{.*})(?P<options>.*)'
)
_re_remove_record = re.compile(
    ur'remove (?P<db>[^.]+)\.(?P<collection>[^ ]+)  query: (?P<query>{.*})(?P<options>.*)'
)
class MongoProfiler(object):

    def __init__(self, db):
        self.db = db
        self.records = []

    def __enter__(self):
        self.prev_level = self.db.profiling_level()
        self.timestamp_threshold = self.get_timestamp_threshold()
        self.db.set_profiling_level(pymongo.ALL)
        return self

    def get_timestamp_threshold(self):
        prev_records = list(self.db.system.profile.find().sort([('ts', pymongo.DESCENDING)]).limit(1))
        if len(prev_records) > 0:
            return prev_records[0]['ts']

    def __exit__(self, type, value, traceback):
        filt = {}
        if self.timestamp_threshold:
            filt = {'ts': {'$gt': self.timestamp_threshold}}
        if self.prev_level == pymongo.ALL:
            skip = 2
        else:
            skip = 0
        stats = self.db.system.profile.find(filt).skip(skip)
        prev_ts = None
        for record in stats:
            r = parse_record(record)
            self._setup_ts_diff(r, prev_ts)
            if r.get('ts'):
                prev_ts = r['ts']
            self.records.append(r)
        self.db.set_profiling_level(self.prev_level)

    def _setup_ts_diff(self, record, prev_ts):
        new_ts = record.get('ts')
        if prev_ts and new_ts:
            diff = new_ts - prev_ts
            record['ts_diff'] = diff.seconds + float(diff.microseconds / 1e6)

    def get_records(self):
        return self.records

    def mark(self, text):
        """ set the marker """
        list(self.db.phony_mongoprofile_marker.find({'text': text}))
        return

class DummyMongoProfiler(list):
    def __init__(self, db):
        pass
    def __enter__(self):
        return self
    def __exit__(self, type, value, traceback):
        pass
    def mark(self, text):
        pass
    def get_records(self):
        return []

def parse_record(record_source):
    record_map = [
        (_re_marker_record, MarkerRecord),
        (_re_command_record, CommandRecord),
        (_re_query_record, QueryRecord),
        (_re_insert_record, InsertRecord),
        (_re_update_record, UpdateRecord),
        (_re_remove_record, RemoveRecord),
        (_re_getmore_record, GetMoreRecord),
    ]
    info = record_source['info']
    # find record by info
    record = None
    for regex, RecordClass in record_map:
        match = regex.search(info)
        if match:
            results = match.groupdict()
            record = RecordClass(record_source)
            record.update(results)
            break
    if not record:
        record = UnknownRecord(record_source)
    # parse record options, if any (see regexps)
    for k in record.keys():
        if k.startswith('options'):
            options = record.pop(k)
            record.update(_parse_record_options(options))
    # convert ints to integer
    for k, v in record.iteritems():
        if isinstance(record[k], basestring):
            try: record[k] = int(record[k])
            except ValueError: pass
    return record



def _parse_record_options(options):
    ret = {}
    option_list = options.strip().split()
    for option in option_list:
        if ':' in option:
            k, v = option.split(':', 1)
        else:
            k, v = option, True
        ret[k] = v
    return ret


class BaseRecord(dict):
    record_type = None
    def short_info(self):
        """ get short info about query results """
        # remove useless data
        record = dict(self)
        useless_fields = 'command info collection query db ts'.split()
        for field in useless_fields:
            if field in record:
                del record[field]
        # print data
        ret = []
        for items in record.iteritems():
            ret.append('%s:%s ' % items)
        return '  '.join(ret)

class CommandRecord(BaseRecord):
    record_type = 'command'
    def __str__(self):
        return str('%(db)s> db.runCommand(%(command)s)' % self)

class QueryRecord(BaseRecord):
    record_type = 'query'
    def __str__(self):
        return str('%(db)s> db.%(collection)s.find(%(query)s)' % self)

class MarkerRecord(BaseRecord):
    record_type = 'marker'
    def __str__(self):
        return str('==== %(text)s ====' % self)

class InsertRecord(BaseRecord):
    record_type = 'insert'
    def __str__(self):
        return str('%(db)s> db.%(collection)s.insert({...})' % self)

class UpdateRecord(BaseRecord):
    record_type = 'update'
    def __str__(self):
        return str('%(db)s> db.%(collection)s.update(%(query)s, {...})' % self)

class RemoveRecord(BaseRecord):
    record_type = 'remove'
    def __str__(self):
        return str('%(db)s> db.%(collection)s.remove(%(query)s)' % self)

class GetMoreRecord(BaseRecord):
    record_type = 'get_more'
    def __str__(self):
        return str('%(db)s> db.%(collection)s.find(%(query)s) *getmore' % self)

class UnknownRecord(BaseRecord):
    record_type = 'unknown'
    pass
