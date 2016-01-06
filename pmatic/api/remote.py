#!/usr/bin/env python
# encoding: utf-8
#
# pmatic - A simple API to to the Homematic CCU2
# Copyright (C) 2016 Lars Michelsen <lm@larsmichelsen.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""Realizes the remote connection to the CCU via HTTP"""

# Add Python 3.x behaviour to 2.7
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

try:
    from urllib.request import urlopen
    from urllib.error import URLError
    from http.client import BadStatusLine
except ImportError:
    from urllib2 import urlopen
    from urllib2 import URLError
    from httplib import BadStatusLine

import json

from .. import PMException, PMConnectionError, utils
from .abstract import AbstractAPI

class RemoteAPI(AbstractAPI):
    """Provides API access via HTTP to the CCU."""
    _session_id = None

    def __init__(self, address, credentials, connect_timeout=10, logger=None, log_level=None):
        self._session_id      = None
        self._address         = None
        self._credentials     = None
        self._connect_timeout = None

        super(RemoteAPI, self).__init__(logger, log_level)

        self._set_address(address)
        self._set_credentials(credentials)
        self._set_connect_timeout(connect_timeout)

        self.login()
        self._init_methods()
        self._register_atexit_handler()


    def _set_address(self, address):
        if not utils.is_string(address):
            raise PMException("Please specify the address of the CCU.")

        # Add optional protocol prefix
        if not address.startswith("https://") and not address.startswith("http://"):
            address = "http://%s" % address

        self._address = address


    def _set_credentials(self, credentials):
        if type(credentials) != tuple:
            raise PMException("Please specify the user credentials to log in to the CCU like this: \"(username, password)\".")
        elif len(credentials) != 2:
            raise PMException("The credentials must be given as tuple of two elements.")
        elif not utils.is_string(credentials[0]):
            raise PMException("The username is of unhandled type.")
        elif not utils.is_string(credentials[1]):
            raise PMException("The username is of unhandled type.")

        self._credentials = credentials


    def _set_connect_timeout(self, timeout):
        if type(timeout) not in [ int, float ]:
            raise PMException("Invalid timeout value. Must be of type int or float.")
        self._connect_timeout = timeout


    def _get_methods_config(self):
        # Can not use API.rega_run_script() here since the method infos are not yet
        # available. User code should use API.rega_run_script().
        response = self.call("rega_run_script",
            _session_id_=self._session_id,
            script="string stderr;\n"
                   "string stdout;\n"
                   "system.Exec(\"cat /www/api/methods.conf\", &stdout, &stderr);\n"
                   "Write(stdout);\n"
        )
        return response.split("\r\n")


    def login(self):
        if self._session_id:
            raise PMException("Already logged in.")

        response = self.call("session_login", username=self._credentials[0],
                                              password=self._credentials[1])
        if response == None:
            raise PMException("Login failed: Got no session id.")
        self._session_id = response


    def logout(self):
        if self._session_id:
            self.call("session_logout", _session_id_=self._session_id)
            self._session_id = None


    def close(self):
        self.logout()


    def get_arguments(self, method, args):
        if "_session_id_" in method["ARGUMENTS"] and self._session_id:
            args["_session_id_"] = self._session_id
        return args


    # The following wrapper allows specific API calls which are needed
    # before the real list of methods is available, so allow
    # it to be not validated and fake the method response.
    def _get_method(self, method_name_int):
        try:
            return super(RemoteAPI, self)._get_method(method_name_int)
        except PMException:
            if method_name_int == "session_login" and not self._methods:
                return {
                    "NAME": "Session.login",
                    "INFO": "Führt die Benutzeranmeldung durch",
                    "ARGUMENTS": [ "username", "password" ],
                }
            elif method_name_int == "rega_is_present" and not self._methods:
                return {
                    "NAME": "ReGa.isPresent",
                    "INFO": "Prüft, ob die Logikschicht (ReGa) aktiv ist",
                    "ARGUMENTS": [ ],
                }
            elif method_name_int == "rega_run_script" and not self._methods:
                return {
                    "NAME": "ReGa.runScript",
                    "INFO": "Führt ein HomeMatic Script aus",
                    "ARGUMENTS": [ "_session_id_", "script" ],
                }
            elif method_name_int == "session_logout" and not self._methods:
                return {
                    "NAME": "Session.logout",
                    "INFO": "Beendet eine Sitzung",
                    "ARGUMENTS": [ "_session_id_" ],
                }
            else:
                raise


    # Runs the provided method, which needs to be one of the methods which are available
    # on the device (with the given arguments) on the CCU.
    def call(self, method_name_int, **kwargs):
        method = self._get_method(method_name_int)
        args   = self.get_arguments(method, kwargs)

        self.debug("CALL: %s ARGS: %r" % (method["NAME"], args))

        json_data = json.dumps({
            "method": method["NAME"],
            "params": args,
        })
        url = "%s/api/homematic.cgi" % self._address

        try:
            self.debug("  URL: %s DATA: %s" % (url, json_data))
            handle = urlopen(url, data=json_data.encode("utf-8"),
                             timeout=self._connect_timeout)
        except Exception as e:
            if type(e) == URLError:
                msg = e.reason
            elif type(e) == BadStatusLine:
                msg = "Request terminated. Is the device rebooting?"
            else:
                msg = e
            raise PMConnectionError("Unable to open \"%s\" [%s]: %s" % (url, type(e).__name__, msg))

        response_txt = ""
        for line in handle.readlines():
            response_txt += line.decode("utf-8")

        http_status = handle.getcode()

        self.debug("  HTTP-STATUS: %d" % http_status)
        if http_status != 200:
            raise PMException("Error %d opening \"%s\" occured: %s" %
                                    (http_status, url, response_txt))

        self.debug("  RESPONSE: %s" % response_txt)
        return self._parse_api_response(method_name_int, response_txt)


    @property
    def address(self):
        return self._address
