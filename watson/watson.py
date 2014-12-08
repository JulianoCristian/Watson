# -*- coding: utf-8 -*-

import os
import json

try:
    from configparser import ConfigParser
except ImportError:
    from ConfigParser import SafeConfigParser as ConfigParser  # noqa

import arrow

WATSON_FILE = os.path.join(os.path.expanduser('~'), '.watson')
WATSON_CONF = os.path.join(os.path.expanduser('~'), '.watson.conf')


class WatsonError(RuntimeError):
    pass


class Watson(object):
    def __init__(self, content=None, filename=WATSON_FILE):
        self.filename = filename
        self.tree = None
        self._current = None

        self._load(content)

    def _load(self, content=None):
        """
        Initialize the class attributes from `content`.

        :param content: If given, should be a dict obtained by parsing a
                        Watson file. If not given, the content is extracted
                        from the Watson file.
        :type content: dict
        """
        if content is None:
            content = self._load_watson_file()

        self.tree = {'projects': content.get('projects', {})}
        self.current = content.get('current')

    def _load_watson_file(self):
        """
        Return the content of the current Watson file as a dict.
        If the file doesn't exist, return an empty dict.
        """
        try:
            with open(self.filename) as f:
                return json.load(f)
        except IOError:
            return {}
        except ValueError as e:
            # If we get an error because the file is empty, we ignore
            # it and return an empty dict. Otherwise, we raise
            # an exception in order to avoid corrupting the file.
            if os.path.getsize(self.filename) == 0:
                return {}
            else:
                raise WatsonError(
                    "Invalid Watson file {}: {}".format(self.filename, e)
                )
        else:
            raise WatsonError(
                "Impossible to open Watson file in {}".format(self.filename)
            )

    def _parse_date(self, date):
        return arrow.Arrow.utcfromtimestamp(date)

    def _format_date(self, date):
        if not isinstance(date, arrow.Arrow):
            date = arrow.get(date)

        return date.timestamp

    @property
    def config(self):
        """
        Return Watson's config as a dict-like object.
        """
        config = ConfigParser()
        config.read(WATSON_CONF)

        if not config.has_option('crick', 'url') \
                or not config.has_option('crick', 'token'):
            raise WatsonError(
                "You must specify a remote URL and a token by putting it in"
                "Watson's config file at '{}'".format(WATSON_CONF)
            )

        return config

    def dump(self):
        """
        Return a new dict which can be saved in the Watson file.
        """
        content = dict(self.tree)

        if self.is_started:
            current = self.current
            content['current'] = {
                'project': current['project'],
                'start': self._format_date(current['start'])
            }

        return content

    def save(self):
        """
        Save the given dict in the Watson file. Create the file in necessary.
        """
        try:
            with open(self.filename, 'w+') as f:
                json.dump(self.dump(), f, indent=2)
        except OSError:
            raise WatsonError(
                "Impossible to open Watson file in {}".format(self.filename)
            )

    @property
    def current(self):
        if not self._current:
            return None

        return dict(self._current)

    @current.setter
    def current(self, value):
        if not value or 'project' not in value:
            self._current = None
            return

        start = value.get('start', arrow.now())

        if isinstance(start, str):
            start = self._parse_date(start)

        self._current = {
            'project': value['project'],
            'start': start
        }

    @property
    def is_started(self):
        return self.current is not None

    def start(self, project):
        if self.is_started:
            raise WatsonError(
                "Project {} is already started.".format(
                    self.current['project']
                )
            )

        if not project:
            raise WatsonError("No project given.")

        self.current = {'project': project}
        return self.current

    def stop(self, message=None):
        if not self.is_started:
            raise WatsonError("No project started.")

        old = self.current
        self.add_frame(
            old['project'], old['start'], arrow.now(),
            message=message
        )
        self.current = None

        return old

    def cancel(self):
        if not self.is_started:
            raise WatsonError("No project started.")

        old_current = self.current
        self.current = None
        return old_current

    def project(self, name):
        """
        Return the project from the projects tree with the given name. The name
        can be separated by '/' for sub-projects.
        """
        project = self.tree
        for name in name.split('/'):
            if name not in project['projects']:
                project['projects'][name] = {'frames': [], 'projects': {}}
            project = project['projects'][name]

        return project

    def projects(self):
        """
        Return the list of all the existing projects, sorted by name.
        """
        def get_projects(project, parent):
            result = []

            for name, child in project.get('projects', {}).items():
                name = parent + name
                result.append(name)
                result += get_projects(child, name + '/')

            return result

        return sorted(get_projects(self.tree, ''))

    def add_frame(self, project, start, stop, message=None):
        """
        Add a new frame to the given project
        """
        frame = {
            'start': self._format_date(start),
            'stop': self._format_date(stop),
        }

        if message:
            frame['message'] = message

        self.project(project)['frames'].append(frame)

    def frames(self):
        """
        Return a list of all the frames, sorted by start time.
        """
        def get_frames(parent, ancestors=''):
            frames = []

            for name, project in parent['projects'].items():
                for raw_frame in project['frames']:
                    frames.append({
                        'project': ancestors + name,

                        'id': raw_frame.get('id'),

                        'start': self._parse_date(raw_frame['start']),
                        'stop': self._parse_date(raw_frame['stop'])
                    })

                frames += get_frames(project, ancestors + name + '/')
            return frames

        return sorted(get_frames(self.tree), key=lambda e: e['start'])

    def push(self, force=False):
        import requests

        config = self.config

        dest = config.get('crick', 'url') + '/frames/'
        token = config.get('crick', 'token')

        frames = tuple(
            {
                'id': f.get('id'),
                'start': str(f['start']),
                'stop': str(f['stop']),
                'project': f['project'].split('/')
            }
            for f in self.frames()
        )

        new_frames = tuple(f for f in frames if f['id'] is None)

        if force:
            existing_frames = tuple(f for f in frames if f['id'] is not None)
        else:
            existing_frames = []

        headers = {
            'content-type': 'application/json',
            'Authorization': "Token {}".format(token)
        }

        if new_frames:
            data = json.dumps({'frames': new_frames})
            try:
                response = requests.post(
                    dest, data, headers=headers
                )
            except requests.ConnectionError:
                raise WatsonError("Unable to reach the server.")

            if response.status_code != 201:
                raise WatsonError(
                    "An error occured with the remote "
                    "server: {}".format(response.json())
                )

            ids = response.json()

            for frame, _id in zip(new_frames, ids):
                frame['id'] = _id
                del frame['project']

        if existing_frames:
            data = json.dumps({'frames': existing_frames})
            try:
                response = requests.put(
                    dest, data, headers=headers
                )
            except requests.ConnectionError:
                raise WatsonError("Unable to reach the server.")

            if response.status_code != 200:
                raise WatsonError(
                    "An error occured with the remote server: "
                    "{}".format(response.json())
                )

        return new_frames
