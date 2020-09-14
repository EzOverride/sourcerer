#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__appname__ = 'Sourcerer'
__author__  = 'EzOverride'
__version__ = "0.1"

import re
import os
import sys
import yaml
import json
import mistune
import argparse
from datetime import datetime
from bs4 import BeautifulSoup
from attackcti import attack_client
from requests.auth import HTTPBasicAuth
from elasticsearch import Elasticsearch
from openpyxl.styles import Color
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule, FormulaRule

CONFIG = yaml.load(open('resources/config.yml', 'r'), Loader=yaml.Loader)


class attackCTI:
    """This class performs all ATT&CK parsing related tasks"""

    def __init__(self, ds_scores):
        """Pull ATT&CK data from MITRE API"""
        print('[*] Pulling ATT&CK data')

        cli = attack_client()
        attack = cli.get_enterprise(stix_format=False)
        self.techniques = cli.remove_revoked(attack['techniques'])
        self.ds_scores = ds_scores

    def to_score(self, number):
        return float(('{0:.2f}'.format(number)))

    def get_techniques(self):
        return self.techniques

    def data_source_score(self, data_source):
        #ds_scores = self.ds_scores
        if data_source.lower() in self.ds_scores:
            return self.ds_scores[data_source.lower()]
        else:
            return [0,0,0,0,0,0,0]

    def get_ds_score(self, data_sources):
        """Retrieves average score of all techniques"""

        score_list = []
        for ds in data_sources:
            score_list.append(self.data_source_score(ds))

        #calculate average of scores
        score = [self.to_score(sum(v) / len(v)) for v in zip(*score_list)]
        return score

    def get_ds_quality_layer(self):
        """ returns an attack data source quality navigator layer """
        print('[*] Generating data source quality layer')

        self.nav_layer = yaml.load(open('resources/navigator_layer.yml', 'r'), Loader=yaml.Loader)
        self.nav_layer['name'] = 'Data Quality'
        self.nav_layer['description'] = 'Data source quality according OSSEM data model'

        for t in self.get_techniques():
            comment = ""
            if 'data_sources' in t:
                scores = self.get_ds_score(t['data_sources'])
                dq_coverage = scores[0]
                dq_timeliness = scores[1]
                dq_retention = scores[2]
                dq_structure = scores[3]
                dq_consistency = scores[4]
                dq_score = scores[5]
            else:
                comment = 'technique has no data sources'
                dq_coverage = 0
                dq_timeliness = 0
                dq_retention = 0
                dq_structure = 0
                dq_consistency = 0
                dq_score = 0

            technique = {
                "techniqueID": t['technique_id'],
                "score": dq_score,
                "comment": comment,
                "enabled": True,
                "metadata": [
                    {"name": "coverage", "value": str(dq_coverage)},
                    {"name": "timeliness", "value": str(dq_timeliness)},
                    {"name": "retention", "value": str(dq_retention)},
                    {"name": "structure", "value": str(dq_structure)},
                    {"name": "consistency", "value": str(dq_consistency)}]}

            self.nav_layer['techniques'].append(technique)

        return self.nav_layer


class mdRenderer(mistune.Renderer):
    def __init__(self, renderer=None, inline=None, block=None, **kwargs):
        super().__init__(**kwargs)
        self.is_data_field = False
        self.is_description = False
        self.data_fields = []
        self.description = None
        self.context = kwargs.get('context')

    def get_description(self):
        """ returns object description """
        return self.description

    def get_data_fields(self):
        """ returns a common information model entity """
        return self.data_fields

    def table_to_dict(self, header, rows):
        """ takes the header and rows list and returns a list of dictionaries"""
        table = []
        headers = [i.text.lower() for i in header]
        for row in rows:
            columns = [i.text for i in row.find_all('td')]
            table.append(dict(zip(headers, columns)))

        return table

    def header(self, text, level, raw=None):
        """ returns the header markdown entries """
        if text == 'Data Fields' or text == 'Data Dictionary':
            self.is_data_field = True
        elif level == 1 and self.context == 'cim':
            self.is_description = True
        elif text == 'Description':
            self.is_description = True
        return text

    def table(self, header, body):
        """ returns table markdown entries """
        if self.is_data_field or self.context == 'ddm':
            header_list = BeautifulSoup(header, 'lxml').find_all('th')
            row_list = BeautifulSoup(body, 'lxml').find_all('tr')
            self.data_fields = self.table_to_dict(header_list, row_list)
            self.is_data_field = False

        return header

    def paragraph(self, text):
        """ returns paragraphs """
        if self.is_description:
            self.description = text
            self.is_description = False
        return text

class ossemParser():
    def __init__(self, profile):
        self.profile = yaml.load(open(profile, 'r'), Loader=yaml.Loader)
        self.data_channels = list(yaml.load_all(open('resources/dcs.yml', 'r'), Loader=yaml.Loader))
        self.cim_entities = []
        self.cim_ignore = ['domain_or_hostname_or_fqdn.md']
        self.data_dictionaries = []
        self.data_dictionaries_ignore = []
        self.ddm_list = []
        self.ddm_ignore = ['object_relationships.md']

    def parse_markdown(self, path):
        """ parser for ossem in markdown """
        for root, dirs, files in os.walk(path):
            for name in files:
                filepath = root + os.sep + name
                if name.endswith('.md') and 'README' not in name:
                    path = root.split('/')
                    cim = 'common_information_model'
                    dd = 'data_dictionaries'
                    ddm = 'detection_data_model'

                    #parse cim
                    if cim in path and name not in self.cim_ignore:
                        renderer = mdRenderer(context='cim')
                        md = mistune.Markdown(renderer=renderer)
                        with open(filepath, 'r') as md_file:
                            md(md_file.read())
                            self.cim_entities.append({
                                'entity': name.split('.')[0],
                                'description': md.renderer.get_description(),
                                'data fields': md.renderer.get_data_fields()})

                    #parse dd
                    elif dd in path and name not in self.data_dictionaries_ignore:
                        dd_path = path[path.index(dd)+1:]
                        os_name = dd_path[0]
                        data_channel = dd_path[1]
                        renderer = mdRenderer(context='dd')
                        md = mistune.Markdown(renderer=renderer)
                        with open(filepath, 'r') as md_file:
                            md(md_file.read())
                            self.data_dictionaries.append({
                                'operating system': os_name,
                                'data channel': data_channel,
                                'description': md.renderer.get_description(),
                                'event': re.sub('event-', '', name.split('.')[0]),
                                'data fields': md.renderer.get_data_fields()})

                    #parse ddm
                    elif ddm in path and name not in self.ddm_ignore:
                        renderer = mdRenderer(context='ddm')
                        md = mistune.Markdown(renderer=renderer)
                        with open(filepath, 'r') as md_file:
                            md(md_file.read())
                            self.ddm_list += md.renderer.get_data_fields()

        return self.ddm_list

    def parse_yaml(self, path):
        """ parser for ossem in yaml """
        self.ddm_list = list(yaml.load_all(open(path+CONFIG['OSSEM_YAML_DDM'], 'r'), Loader=yaml.Loader))
        self.data_dictionaries = list(yaml.load_all(open(path+CONFIG['OSSEM_YAML_DDS'], 'r'), Loader=yaml.Loader))
        self.cim_entities = list(yaml.load_all(open(path+CONFIG['OSSEM_YAML_CIM'], 'r'), Loader=yaml.Loader))

        return self.ddm_list

    def enrich_ddm(self):
        """ iterate over ddm entries and calculate data quality scores """

        for row in self.ddm_list:
            event_name = row['eventid']

            # init data quality scoring
            row['coverage'] = 0
            row['timeliness'] = 0
            row['retention'] = 0
            row['structure'] = 0
            row['consistency'] = 0
            row['score'] = 0
            row['data channel'] = None
            row['comment'] = ''

            # TODO: some events have the same name across diferent platforms
            #       in the future OSSEM DDM will need to include OS so that we
            #       can filter accordingly, otherwise the script might
            #       incorrectly match an event...

            # find ddm entries for events with data dictionaries
            dd_matches = list(filter(lambda entry: entry['event'] == event_name, self.data_dictionaries))
            if dd_matches:
                dd = dd_matches[0]
                data_channel = dd['data channel']
                dcs_matches = list(filter(lambda entry: entry['data channel'] == data_channel, self.data_channels))

                #retrieve data channels scores, otherwise set them to zero
                if dcs_matches:
                    dcs = dcs_matches[0]
                    row['coverage'] = int(dcs['coverage'])
                    row['timeliness'] = int(dcs['timeliness'])
                    row['retention'] = int(dcs['retention'])
                    row['data channel'] = dcs['data channel']
                else:
                    row['comment'] = 'data channel not found'

                #calculate structure score
                matched_fields = 0
                total_fields = 0

                entities = [
                    row['source data object'],
                    row['destination data object']]

                invalid = False
                missing = 0
                for entity in entities:
                    match_cim = list(filter(lambda entry: entry['entity'] == entity, self.cim_entities))

                    if invalid:
                        continue
                    elif not entity:
                        missing += 1
                        if missing == 2:
                            row['comment'] = 'both entities are missing'
                            invalid = True
                    elif match_cim and not invalid and missing < 2:
                        match = match_cim[0]
                        if match['entity'] in self.profile:
                            for field in self.profile[match['entity']]:
                                total_fields += 1
                                field_matches = list(filter(lambda entry: entry['standard name'] == field, dd['data fields']))
                                if field_matches:
                                    matched_fields += 1
                            #row['comment'] += ('{} matched {}/{} ').format(entity, matched_fields, total_fields)
                        else:
                            invalid = True
                            row['comment'] = ('{} not found in profiles').format(entity)
                    else:
                        invalid = True
                        row['comment'] = ('{} not found in CIM').format(entity)

                if matched_fields > 0 and not invalid:
                    score = (float(matched_fields) / float(total_fields)) * 100

                    if score > 0 and score <= 25:
                        structure_score = 1
                    elif score >= 26 and score <= 50:
                        structure_score = 2
                    elif score >= 51 and score <= 75:
                        structure_score = 3
                    elif score >= 76 and score <= 99:
                        structure_score = 4
                    elif score == 100:
                        structure_score = 5
                    row['structure'] = structure_score

                #calculate consistency score
                total_fields_count = len(dd['data fields'])
                standard_fields_count = 0

                for field in dd['data fields']:
                    if field['standard name']:
                        standard_fields_count += 1

                    score = (standard_fields_count / total_fields_count) * 100

                    if score >= 0 and score <= 50:
                        consistency_score = 1
                    elif score >= 51 and score <= 99:
                        consistency_score = 3
                    elif score == 100:
                        consistency_score = 5

                row['consistency'] = consistency_score

                #calculate final score
                average_score = sum((
                    row['coverage'],
                    row['timeliness'],
                    row['retention'],
                    row['structure'],
                    row['consistency'])) / 5
                row['score'] = average_score

            else:
                row['comment'] = 'data dictionary not found'

        return self.ddm_list

    def export_to_xlsx(self, path):
        """Generate XLSX version of the detection data model"""

        wb = Workbook()
        ws = wb.active
        ws.append([
            'ATT&CK Data Source',
            'Sub Data Source',
            'Source Data Object',
            'Relationship',
            'Destination Data Object',
            'EventID',
            'Data Channel',
            'Coverage',
            'Timeliness',
            'Retention',
            'Structure',
            'Consistency',
            'Score',
            'Comment'])

        rows = 0
        for entry in self.ddm_list:
            rows += 1

            ws.append([
                entry['att&ck data source'],
                entry['sub data source'],
                entry['source data object'],
                entry['relationship'],
                entry['destination data object'],
                entry['eventid'],
                entry['data channel'],
                entry['coverage'],
                entry['timeliness'],
                entry['retention'],
                entry['structure'],
                entry['consistency'],
                entry['score'],
                entry['comment']])

        #add table
        table = Table(displayName="DDM", ref="A1:N{}".format(rows+1))
        style = TableStyleInfo(name="TableStyleLight15", showRowStripes=True)
        table.tableStyleInfo = style
        ws.add_table(table)

        #add conditional formating
        ws.conditional_formatting.add('H2:M10000',
            ColorScaleRule(
                start_type='min', start_color='F8696B',
                mid_type='percentile', mid_value=50, mid_color='FFEB84',
                end_type='max', end_color='63BE7B'))

        #write new ddm entry
        dt = datetime.now().strftime("%Y%m%d_%H%M%S")

        if not os.path.exists(path):
            os.makedirs(path)

        wb.save('{}ddm_enriched_{}.xlsx'.format(path, dt))
        print('[*] Saved Excel to {}ddm_enriched_{}.xlsx'.format(path, dt))

    def export_to_yaml(self, path):
        """ generates a yaml version of OSSEM data """

        ddm_yaml = yaml.dump_all(self.ddm_list, sort_keys=False)
        cim_yaml = yaml.dump_all(self.cim_entities, sort_keys=False)
        dds_yaml = yaml.dump_all(self.data_dictionaries, sort_keys=False)

        dt = datetime.now().strftime("%Y%m%d_%H%M%S")

        if not os.path.exists(path):
            os.makedirs(path)

        ddm_yaml_file = open('{}ddm_{}.yml'.format(path, dt), 'w')
        ddm_yaml_file.write(ddm_yaml)
        ddm_yaml_file.close()
        print('[*] Created {}ddm_{}.yml'.format(path, dt))

        cim_yaml_file = open('{}cim_{}.yml'.format(path, dt), 'w')
        cim_yaml_file.write(cim_yaml)
        cim_yaml_file.close()
        print('[*] Created {}cim_{}.yml'.format(path, dt))

        dds_yaml_file = open('{}dds_{}.yml'.format(path, dt), 'w')
        dds_yaml_file.write(dds_yaml)
        dds_yaml_file.close()
        print('[*] Created {}dds_{}.yml'.format(path, dt))

        return True

    def export_to_layer(self, path):
        """ generates a json navigator layer of OSSEM data """
        ds_scores = self.get_ds_scores()
        attack = attackCTI(ds_scores)
        layer = attack.get_ds_quality_layer()

        if not os.path.exists(path):
            os.makedirs(path)

        dt = datetime.now().strftime("%Y%m%d_%H%M%S")
        layer_file = open('{}ds_layer_{}.json'.format(path, dt), 'w')
        layer_file.write(json.dumps(layer))
        layer_file.close()
        print('[*] Created {}ds_layer_{}.json'.format(path, dt))

    def get_data_channels(self):
        """ return data channels """
        return self.data_channels

    def get_cim_entities(self):
        """ return flatten cim list """
        result = []

        #flatten cim into a list
        for entity in self.cim_entities:
            for field in entity['data fields']:

                #check if entity is relevant
                relevant = False
                if entity['entity'] in self.profile:
                    if field['standard name'] in self.profile[entity['entity']]:
                        relevant = True

                result.append({
                    'entity': entity['entity'],
                    'standard name': field['standard name'],
                    'type': field['type'],
                    'description': field['description'],
                    'sample value': field['sample value'],
                    'relevant': relevant})

        return result

    def get_dd_list(self):
        """ return flatten data dictionaries """
        result = []

        for data in self.data_dictionaries:
            data_channel = data['data channel']
            operating_system = data['operating system']
            event = data['event']

            for field in data['data fields']:
                result.append({
                    'data channel': data_channel,
                    'operating system': operating_system,
                    'event': event,
                    'standard name': field['standard name'],
                    'field name': field['field name'],
                    'type': field['type'],
                    'description': field['description'],
                    'sample value': field['sample value']})

        return result

    def get_ds_scores(self):
        """Returns a summary of scores by data source"""
        data_sources = {}
        temp_sources = {}

        #build a dict object to store a summary of data source scores
        for entry in self.ddm_list:
            dc = entry['att&ck data source']

            if dc == 0:
                #skip non covered data sources, to avoid polluting the average
                continue

            if dc in data_sources:
                temp_sources[dc].append((
                    entry['coverage'],
                    entry['timeliness'],
                    entry['retention'],
                    entry['structure'],
                    entry['consistency']))
            else:
                temp_sources[dc] = [(
                    entry['coverage'],
                    entry['timeliness'],
                    entry['retention'],
                    entry['structure'],
                    entry['consistency'])]

        #calculate data quality average for the five dimensions
        for ds,dq in temp_sources.items():
            dq_avg = [sum(v) / len(v) for v in zip(*dq)]
            dq_score = sum(dq_avg) / len(dq_avg)
            dq_avg.append(dq_score)
            data_sources[ds.lower()] = dq_avg

        return data_sources


class Elastic:
    def __init__(self):
        self.es = Elasticsearch(
            ['{}:{}'.format(CONFIG['ELASTIC_SERVER'], CONFIG['ELASTIC_PORT'])],
            http_auth=(CONFIG['ELASTIC_USER'],CONFIG['ELASTIC_PASS']))

    def create(self, index, data):
        print('[*] Creating elastic index {}'.format(index))

        #delete index if exists
        try:
            self.es.indices.delete(index)
        except:
            pass

        #create index
        self.es.indices.create(index)

        #pupulate index
        for entry in data:
            self.es.index(index=index, doc_type='entry', body=entry)

        return True


if __name__ == "__main__":
    logo = """\                                                                                               
                  :                                                                            
           .     t#,     :                        .,         ,;                   ,;           
          ;W    ;##W.    Ef       j.              ,Wt       f#i j.               f#i j.         
         f#E   :#L:WE    E#t      EW,            i#D.     .E#t  EW,            .E#t  EW,        
       .E#f   .KG  ,#D   E#t  L#L E##j          f#f      i#W,   E##j          i#W,   E##j       
      iWW;    EE    ;#f  E#t  #Ei E###D.      .D#i      L#D.    E###D.       L#D.    E###D.     
     L##Lffi f#.     t#i E#t  fij E#jG#W;    :KW,     :K#Wfff;  E#jG#W;    :K#Wfff;  E#jG#W;    
    tLLG##L  :#G     GK  E#t  L#j E#t t##f   t#f      i##WLLLLt E#t t##f   i##WLLLLt E#t t##f   
      ,W#i    ;#L   LW.  E#t  L#L E#t  :K#E:  ;#G      .E#L     E#t  :K#E:  .E#L     E#t  :K#E: 
     j#E.      t#f f#:   E#tf#EE: E#KDDDD###i  :KE.      f#E:   E#KDDDD###i   f#E:   E#KDDDD###i
   .D#j         f#D#;    E###f    E#f,t#Wi,,,   .DW:      ,WW;  E#f,t#Wi,,,    ,WW;  E#f,t#Wi,,,
  ,WK,           G#t     E#K,     E#t  ;#W:       L#,      .D#; E#t  ;#W:       .D#; E#t  ;#W:  
  EG.             t      EL       DWi   ,KK:       jt        tt DWi   ,KK:        tt DWi   ,KK: 
  ,                      :                                                                     

"""
    print(logo)
    parser = argparse.ArgumentParser(description='A tool to mapped ATT&CK data source coverage, utilising OSSEM.')
    parser.add_argument('-o', '--ossem', 
        help='path to import OSSEM markdown')
    parser.add_argument('-y', '--ossem-yaml',
        help='path to import OSSEM yaml')
    parser.add_argument('-p', '--profile',
        help='path to CIM profile',
        default='profiles/default.yml')
    parser.add_argument('--excel',
        help='export OSSEM DDM to excel',
        action='store_true')
    parser.add_argument('--elastic',
        help='export OSSEM data models to elastic',
        action='store_true')
    parser.add_argument('--yaml',
        help='export OSSEM data models to yaml',
        action='store_true')
    parser.add_argument('--layer',
        help='export OSSEM data models to navigator layer',
        action='store_true')
    args = parser.parse_args()

    if not args.excel and not args.elastic and not args.yaml and not args.layer:
        print('[!] You forgot to select an output. Check the available output arguments with --help.')
        sys.exit()

    print('[*] Profile path: {}'.format(args.profile))
    ossem = ossemParser(args.profile)

    if args.ossem:
        print('[*] Parsing OSSEM from markdown')
        ddm_list = ossem.parse_markdown(args.ossem)
    elif args.ossem_yaml:
        print('[*] Parsing OSSEM from YAML')
        ddm_list = ossem.parse_yaml(args.ossem_yaml)

    if args.excel:
        print('[*] Exporting OSSEM DDM to Excel')
        ddm = ossem.enrich_ddm()
        path = 'output/'
        ossem.export_to_xlsx(path)

    elif args.elastic:
        print('[*] Exporting OSSEM to Elastic')
        es = Elastic()
        es.create('ossem.ddm', ossem.enrich_ddm())
        es.create('ossem.cim', ossem.get_cim_entities())
        es.create('ossem.dds', ossem.get_dd_list())
        es.create('ossem.dcs', ossem.get_data_channels())

    elif args.yaml:
        print('[*] Exporting OSSEM to YAML')
        path = 'output/'
        ossem.export_to_yaml(path)

    elif args.layer:
        print('[*] Exporting OSSEM to ATT&CK Naviagator Layer')
        path = 'output/'
        ossem.enrich_ddm()
        ossem.export_to_layer(path)
