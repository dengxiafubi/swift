# Copyright (c) 2010 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import collections
from urllib import unquote

from swift.common.utils import split_path

month_map = '_ Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()

class AccessLogProcessor(object):

    def __init__(self, conf):
        self.server_name = conf.get('server_name', 'proxy')
        self.lb_private_ips = [x.strip() for x in \
                               conf.get('lb_private_ips', '').split(',')\
                               if x.strip()]
        self.service_ips = [x.strip() for x in \
                            conf.get('service_ips', '').split(',')\
                            if x.strip()]

    def log_line_parser(self, raw_log):
        '''given a raw access log line, return a dict of the good parts'''
        d = {}
        try:
            (_,
            server,
            client_ip,
            lb_ip,
            timestamp,
            method,
            request,
            http_version,
            code,
            referrer,
            user_agent,
            auth_token,
            bytes_in,
            bytes_out,
            etag,
            trans_id,
            headers,
            processing_time) = (unquote(x) for x in raw_log[16:].split(' '))
            if server != self.server_name:
                raise ValueError('incorrect server name in log line')
            (version,
            account,
            container_name,
            object_name) = split_path(request, 2, 4, True)
            if container_name is not None:
                container_name = container_name.split('?', 1)[0]
            if object_name is not None:
                object_name = object_name.split('?', 1)[0]
            account = account.split('?', 1)[0]
            query = None
            if '?' in request:
                request, query = request.split('?', 1)
                args = query.split('&')
                # Count each query argument. This is used later to aggregate
                # the number of format, prefix, etc. queries.
                for q in args:
                    if '=' in q:
                        k, v = q.split('=', 1)
                    else:
                        k = q
                    # Certain keys will get summmed in stats reporting
                    # (format, path, delimiter, etc.). Save a "1" here
                    # to indicate that this request is 1 request for
                    # its respective key.
                    d[k] = 1
        except ValueError:
            pass
        else:
            d['client_ip'] = client_ip
            d['lb_ip'] = lb_ip
            d['method'] = method
            d['request'] = request
            if query:
                d['query'] = query
            d['http_version'] = http_version
            d['code'] = code
            d['referrer'] = referrer
            d['user_agent'] = user_agent
            d['auth_token'] = auth_token
            d['bytes_in'] = bytes_in
            d['bytes_out'] = bytes_out
            d['etag'] = etag
            d['trans_id'] = trans_id
            d['processing_time'] = processing_time
            day, month, year, hour, minute, second = timestamp.split('/')
            d['day'] = day
            month = ('%02s' % month_map.index(month)).replace(' ', '0')
            d['month'] = month
            d['year'] = year
            d['hour'] = hour
            d['minute'] = minute
            d['second'] = second
            d['tz'] = '+0000'
            d['account'] = account
            d['container_name'] = container_name
            d['object_name'] = object_name
            d['bytes_out'] = int(d['bytes_out'].replace('-','0'))
            d['bytes_in'] = int(d['bytes_in'].replace('-','0'))
            d['code'] = int(d['code'])
        return d

    def process(self, obj_stream):
        '''generate hourly groupings of data from one access log file'''
        hourly_aggr_info = {}
        for line in obj_stream:
            line_data = self.log_line_parser(line)
            if not line_data:
                continue
            account = line_data['account']
            container_name = line_data['container_name']
            year = line_data['year']
            month = line_data['month']
            day = line_data['day']
            hour = line_data['hour']
            bytes_out = line_data['bytes_out']
            bytes_in = line_data['bytes_in']
            method = line_data['method']
            code = int(line_data['code'])
            object_name = line_data['object_name']
            client_ip = line_data['client_ip']

            op_level = None
            if not container_name:
                op_level = 'account'
            elif container_name and not object_name:
                op_level = 'container'
            elif object_name:
                op_level = 'object'

            aggr_key = (account, year, month, day, hour)
            d = hourly_aggr_info.get(aggr_key, {})
            if line_data['lb_ip'] in self.lb_private_ips:
                source = 'service'
            else:
                source = 'public'
            
            if line_data['client_ip'] in self.service_ips:
                source = 'service'

            d[(source, 'bytes_out')] = d.setdefault((source, 'bytes_out'), 0) + \
                                       bytes_out
            d[(source, 'bytes_in')] = d.setdefault((source, 'bytes_in'), 0) + \
                                      bytes_in

            d['format_query'] = d.setdefault('format_query', 0) + \
                                line_data.get('format', 0)
            d['marker_query'] = d.setdefault('marker_query', 0) + \
                                line_data.get('marker', 0)
            d['prefix_query'] = d.setdefault('prefix_query', 0) + \
                                line_data.get('prefix', 0)
            d['delimiter_query'] = d.setdefault('delimiter_query', 0) + \
                                   line_data.get('delimiter', 0)
            path = line_data.get('path', 0)
            d['path_query'] = d.setdefault('path_query', 0) + path

            code = '%dxx' % (code/100)
            key = (source, op_level, method, code)
            d[key] = d.setdefault(key, 0) + 1

            hourly_aggr_info[aggr_key] = d
        return hourly_aggr_info
