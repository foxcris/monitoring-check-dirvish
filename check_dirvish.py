#!/usr/bin/python3

"""Nagios plugin to check the existence and freshness of a valid backup"""

import argparse
import logging
import subprocess
import os
import datetime
import collections

try:
	import nagiosplugin
except ImportError as e:
    print("Please install python3-nagiosplugin")
    raise e

try:
    import dateutil.parser
except ImportError as e:
    print("Please install python3-dateutil")
    raise e


_log = logging.getLogger('nagiosplugin')


class E_PathNotAccessible(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return "Basepath %r is not accessible" %repr(self.value)

class E_PathNoDir(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return "Basepath %r is not a directory" %repr(self.value)

class E_HistoryFileNotFound(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return "HistoryFile %r not found. Is there at last one Backup?" %repr(self.value)

class E_BackupNotValid(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return "Backup is not valid. %s" % (self.value)

class Backup(nagiosplugin.Resource):
    """Domain model: Dirvish vaults"""

    def __init__(self, vault, base_path):
        self.vault = vault
        self.base_path = base_path

    @property
    def name(self):
        """formatting the Testname (will be formatted as uppercase letters)"""
        return "%s %s" % (self.__class__.__name__, self.vault.split('.')[0])


    def check_path_accessible(self, directory):
        _log.debug("Check if %r is accessible and a directory", directory)
        if not os.access(directory, os.R_OK | os.X_OK):
            raise E_PathNotAccessible(directory)
        if not os.path.isdir(directory):
            raise E_PathNoDir(directory)
        return

    def backups(self):
        """Returns a backups List with a dictionary to every Backupattempt"""
        _log.debug('Finding the latest backup for vault "%s"', self.vault)
        self.check_path_accessible(self.base_path)
        self.vault_base_path = os.path.join(self.base_path, self.vault)
        self.check_path_accessible(self.vault_base_path)
        self.history_file = os.path.join(self.vault_base_path, 'dirvish', 'default.hist')
        _log.debug('Check for %r' % self.history_file)
        resultL = list()
        if not os.access(self.history_file, os.R_OK):
            raise E_HistoryFileNotFound(self.history_file)
        with open(self.history_file) as histfile:
            lines = histfile.readlines()
        for entry in reversed(lines):
            try:
                last_entry = entry.strip()
                image = dict()
                image['image'] = last_entry.split('\t')[0]
                image['histfile'] = True
                _log.info("Found next backup in %r", image['image'])
            except Exception as e:
                _log.error("Something unexpected happened, while reading file %r", self.history_file)
                next
            yield(image['image'])

    def parse_backup(self, backup, parameterL = ['status', 'backup-begin', 'backup-complete']):
        """ Check the last backup for validity.
            Returns a dict with found keys in parameterL.
            All parameters are treated as caseinsensitive via str.casefold
        """
        _log.debug('Parsing backup: %r', backup)
        _parameterL = [ s.casefold() for  s in parameterL ]
        _log.debug("Searching for parameters %r", _parameterL)
        _resultD = dict()
        backup_image = os.path.join(self.vault_base_path, backup)
        self.check_path_accessible(backup_image)
        self.check_path_accessible(os.path.join(backup_image, 'tree'))
        summary_file = os.path.join(backup_image, 'summary')
        if not os.access(summary_file, os.R_OK):
            raise E_BackupNotValid('could not access summary file')
        with open(summary_file) as summary:
            for line in summary.readlines():
                parts = line.strip().split(': ')
                if len(parts) >= 2:
                    # we have a definition
                    parameter = parts[0]
                    value = " ".join(parts[1:])
                    _log.debug('Found parameter %r with value %r', parameter.casefold(), value)
                    parameter_casefold = parameter.casefold()
                    if parameter_casefold in _parameterL:
                        _log.debug("Adding parameter %r to returnDict", parameter_casefold)
                        _resultD[parameter_casefold] = value
        _log.info("parsed Backup to: %r", _resultD)
        return _resultD

    def check_backups(self):
        for backup in self.backups():
            try:
                parsed_backup = self.parse_backup(backup, ['status', 'backup-begin', 'backup-complete'])
            except E_PathNotAccessible as e:
                _log.debug("Exception thrown: %s", e)
                continue
            begin = dateutil.parser.parse(parsed_backup['backup-begin'])
            _log.debug("Backup begin %r to %r", parsed_backup['backup-begin'], begin)
            end = dateutil.parser.parse(parsed_backup['backup-complete'])
            _log.debug("Backup end %r to %r", parsed_backup['backup-complete'], end)
            dur = end - begin
            _log.debug("Duration is: %s", dur)
            if self.duration is None:
                self.duration = dur.total_seconds()
                _log.info('Gathered last duration to %s hours', dur)
            if self.last_try is None:
                age = datetime.datetime.now() - begin
                self.last_try = age.total_seconds()
                _log.info('Gathered last_try to %s days', age)
            if parsed_backup['status'].casefold() == "success":
                if self.last_success is None:
                    age = datetime.datetime.now() - begin
                    self.last_success = age.total_seconds()
                    _log.info('Gathered last_success to %s', age)
            if self.duration and self.last_try and self.last_success:
                _log.info('I have all required Informations. Exiting backup loop')
                break


    def probe(self):
        """Create check metric for Backups

        'last_success' is the metric for the lastsuccessful backup
        'last_try' is the metric for the last try
        'duraction' is the metric for the duration of the last backup
        """
        self.duration = None
        self.last_try = None
        self.last_success = None

        self.check_backups()

        yield nagiosplugin.Metric('last_success', self.last_success, uom='s', min=0)
        yield nagiosplugin.Metric('last_try', self.last_try, uom='s', min=0)
        yield nagiosplugin.Metric('duration', self.duration, uom='s', min=0)

class Duration_Fmt_Metric(object):
    """ this class only use is to format a metric containing timedeltas
        to print a human readable output like 7:30 or 6Y7d. """

    def __init__(self, fmt_string):
        self.fmt_string = fmt_string

    @staticmethod
    def seconds_human_readable(seconds):
        year   = 60*60*24*365
        month  = 60*60*24*30
        day    = 60*60*24
        hour   = 60*60
        minute = 60

        string = ""
        remaining_unitcount = 2
        years, remain = divmod(seconds, year)
        if years > 0:
            string += "%sY" % years
            seconds = remain
            remaining_unitcount -= 1
            if remaining_unitcount <=0:
                 return string
        months, remain = divmod(seconds, month)
        if months > 2:
            string += "%sM" % months
            seconds = remain
            remaining_unitcount -= 1
            if remaining_unitcount <=0:
                 return string
        days, remain = divmod(seconds, day)
        if days > 0:
            string += "%sd" % days
            seconds = remain
            remaining_unitcount -= 1
            if remaining_unitcount <=0:
                 return string
        hours, seconds = divmod(seconds, hour)
        minutes, seconds = divmod(seconds, minute)
        if remaining_unitcount > 1:
            string += "{0:0>2}h{1:0>2}".format(hours, minutes)
        else:
            string += "{0:0>2}h".format(hours)
        assert seconds < 60
        return string

    def __call__(self, metric, context):
        assert metric.uom == "s"
        valueunit = self.seconds_human_readable(int(metric.value))
        return self.fmt_string.format(
            name=metric.name, value=metric.value, uom=metric.uom,
            valueunit=valueunit, min=metric.min, max=metric.max)


@nagiosplugin.guarded
def main():
    argp = argparse.ArgumentParser()
    argp.add_argument('-w', '--warning', metavar='RANGE',
                      help='warning if backup age is outside RANGE in seconds'),
    argp.add_argument('-c', '--critical', metavar='RANGE',
                      help='critical if backup age is outside RANGE in seconds')
    argp.add_argument('-v', '--verbose', action='count', default=0,
                      help='increase output verbosity (use up to 3 times)')
    argp.add_argument('-t', '--timeout', default=10,
                      help='abort execution after TIMEOUT seconds')
    argp.add_argument('--base-path', default="/srv/backup/",
                      help="Path to the bank of the vault (/srv/backup)")
    argp.add_argument('--max-duration', default=12.0, metavar='RANGE',
                      help="max time in hours to take a backup (12.0) in seconds")
    argp.add_argument('vault', help='Name of the vault to check')
    args = argp.parse_args()
    check = nagiosplugin.Check(
        Backup(args.vault, args.base_path),
        nagiosplugin.ScalarContext('last_success', args.warning, args.critical,
                                   Duration_Fmt_Metric('Last successful backup is {valueunit} old')),
        nagiosplugin.ScalarContext('last_try', args.warning, args.critical,
                                   Duration_Fmt_Metric('Last backup tried {valueunit} ago')),
        nagiosplugin.ScalarContext('duration', args.warning, args.critical,
                                   Duration_Fmt_Metric('Last backuprun took {valueunit}')))
    check.main(args.verbose, args.timeout)

if __name__ == '__main__':
    main()
