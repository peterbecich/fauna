import os, re, time, datetime, csv, sys
import numpy as np
import rethinkdb as r
from Bio import SeqIO
from Bio import AlignIO
from upload import upload
from upload import parser
from unidecode import unidecode

parser.add_argument('--upload_directory', default=False, action="store_true", help='upload all xls and fasta files in directory')
parser.add_argument('--vtype', default=None, help="type of virus, if applicable")
parser.add_argument('--subtype', default=None, help="subtype of virus")
parser.add_argument('--lineage', default=None, help="lineage of virus")

class flu_upload(upload):
    def __init__(self, **kwargs):
        upload.__init__(self, **kwargs)
        self.grouping_upload_fields = ['vtype', 'subtype', 'lineage']

        # patterns from the subtype and lineage fields in the GISAID fasta file
        self.patterns = {('a / h1n1', 'pdm09'): ('a', 'h1n1', 'seasonal_h1n1pdm'),
                    ('a / h1n2', ''): ('a', 'h1n2', 'seasonal_h1n2'),
                    ('a / h1n2', 'seasonal'): ('a', 'h1n2', 'seasonal_h1n2'),
                    ('a / h2n2', ''): ('a', 'h2n2', 'undetermined'),
                    ('a / h3n2', ''): ('a', 'h3n2', 'seasonal_h3n2'),
                    ('a / h3n2', 'seasonal'): ('a', 'h3n2', 'seasonal_h3n2'),
                    ('a / h3n3', ''): ('a', 'h3n3', 'undetermined'),
                    ('a / h5n1', ''): ('a', 'h5n1', 'undetermined'),
                    ('a / h5n6', ''): ('a', 'h5n6', 'undetermined'),
                    ('a / h6n1', ''): ('a', 'h6n1', 'undetermined'),
                    ('a / h7n1', ''): ('a', 'h7n1', 'undetermined'),
                    ('a / h7n2', ''): ('a', 'h7n2', 'undetermined'),
                    ('a / h7n3', ''): ('a', 'h7n3', 'undetermined'),
                    ('a / h7n7', ''): ('a', 'h7n7', 'undetermined'),
                    ('a / h7n9', ''): ('a', 'h7n9', 'undetermined'),
                    ('a / h9n2', ''): ('a', 'h9n2', 'undetermined'),
                    ('a / h10n7', ''): ('a', 'h10n7', 'undetermined'),
                    ('a / h10n8', ''): ('a', 'h10n8', 'undetermined'),
                    ('a / h11', ''): ('a', 'h11', 'undetermined'),
                    ('b / h0n0', 'victoria'): ('b', 'undetermined', 'seasonal_vic'),
                    ('b / h0n0', 'yamagata'): ('b', 'undetermined', 'seasonal_yam'),
                    ('b', 'victoria'): ('b', 'undetermined', 'seasonal_vic'),
                    ('b', 'yamagata'): ('b', 'undetermined', 'seasonal_yam')}
        self.outgroups = {lineage: SeqIO.read('source-data/'+lineage+'_outgroup.gb', 'genbank') for lineage in ['H3N2', 'H1N1pdm', 'Vic', 'Yam']}
        self.outgroup_patterns = {'H3N2': ('a', 'h3n2', 'seasonal_h3n2'),
                                  'H1N1': ('a', 'h1n1', 'seasonal_h1n1'),
                                  'H1N1pdm': ('a', 'h1n1', 'seasonal_h1n1pdm'),
                                  'Vic': ('b', 'undetermined', 'seasonal_vic'),
                                  'Yam': ('b', 'undetermined', 'seasonal_yam')}
        self.virus_to_sequence_transfer_fields = ['submission_date']
        self.groups = set()
        self.groups_unknown = 0
        self.ages = set()
        self.originating_labs = set()
        self.submitting_labs = set()

    def parse(self, path, fname, upload_directory, **kwargs):
        '''
        '''
        viruses, sequences = [], []
        if upload_directory:
            import glob
            for xls_fname, fasta_fname in zip(glob.glob(path + "gisaid*.xls"), glob.glob(path + "gisaid*.fasta")):
                parsed = self.parse_files(xls_fname, fasta_fname, **kwargs)
                viruses.extend(parsed[0])
                sequences.extend(parsed[1])
        else:
            fasta_fname = path + fname + ".fasta"
            xls_fname = path + fname + ".xls"
            viruses, sequences = self.parse_files(xls_fname, fasta_fname, **kwargs)
        print("Parsed total of " + str(len(viruses)) + " viruses and " + str(len(sequences)) + " sequences from files")
        return viruses, sequences

    def parse_files(self, xls_fname, fasta_fname, **kwargs):
        '''
        parse linked xls and fasta downloaded from gisaid
        '''
        viruses = self.parse_gisaid_xls_file(xls_fname, **kwargs)
        sequences = self.parse_fasta_file(fasta_fname, **kwargs)
        print("Parsed " + str(len(viruses)) + " viruses and " + str(len(sequences)) + " sequences from files", fasta_fname, xls_fname)
        return viruses, sequences

    def parse_fasta_file(self, fasta, **kwargs):
        '''
        Parse FASTA file with default header formatting
        :return: list of documents(dictionaries of attributes) to upload
        '''
        sequences = []
        try:
            handle = open(fasta, 'r')
        except IOError:
            raise Exception(fasta, "not found")
        else:
            for record in SeqIO.parse(handle, "fasta"):
                content = list(map(lambda x: x.strip(), record.description.replace(">", "").split('|')))
                s = {key: content[ii] if ii < len(content) else "" for ii, key in sequence_fasta_fields.items()}
                s['sequence'] = str(record.seq)
                s = self.add_sequence_fields(s, **kwargs)
                sequences.append(s)
            handle.close()
        return sequences

    def parse_gisaid_xls_file(self, xls, xls_fields_wanted, **kwargs):
        '''
        parse excel file using pandas
        :return: list of documents(dictionaries of attributes) to upload
        '''
        import pandas
        try:
            handle = open(xls, 'rb')
        except IOError:
            raise Exception(xls, "not found")
        else:
            df = pandas.read_excel(handle)
            df = df.where((pandas.notnull(df)), None)  # convert Nan type to None
            viruses = df.to_dict('records')
            viruses = [{new_field: v[old_field] if old_field in v else None for new_field, old_field in xls_fields_wanted} for v in viruses]
            viruses = [self.add_virus_fields(v, **kwargs) for v in viruses]
        return viruses

    def format(self, documents, **kwargs):
        '''
        format virus information in preparation to upload to database table
        Flu needs to also format country
        '''
        self.define_countries()
        self.define_regions()
        self.define_strain_fixes()
        for doc in documents:
            if 'strain' in doc:
                if 'gisaid_location' in doc:
                    doc['strain'], doc['gisaid_strain'] = self.fix_name(doc['strain'], doc['gisaid_location'])
                else:
                    doc['strain'], doc['gisaid_strain'] = self.fix_name(doc['strain'], None)
            else:
                print("Missing strain name!")
            self.fix_casing(doc)
            self.fix_age(doc)
            self.determine_group_fields(doc, **kwargs)
            self.format_date(doc)
            self.format_country(doc)
            self.format_place(doc)
            self.format_region(doc)
            self.rethink_io.check_optional_attributes(doc, [])

    def filter(self, documents, **kwargs):
        remove_labels = ['duck', 'environment', 'Environment', 'shoveler']
        result_documents = [doc for doc in documents if all(label not in doc['strain'] for label in remove_labels)]
        return result_documents

    def fix_casing(self, doc):
        for field in ['originating_lab', 'submitting_lab']:
            if field in doc and doc[field] is not None:
                doc[field] = doc[field].replace(' ', '_').replace('-', '_').lower()
        for field in ['gender', 'host', 'locus']:
            if field in doc and doc[field] is not None:
                doc[field] = self.camelcase_to_snakecase(doc[field])
        if 'accession' in doc and doc['accession'] is not None:
            doc['accession'] = 'EPI' + doc['accession']

    def fix_age(self, doc):
        if 'Host_Age' in doc:
            doc['age'] = None
            if doc['Host_Age'] is not None:
                doc['age'] = str(int(doc['Host_Age']))
            del doc['Host_Age']
            if 'Host_Age_Unit' in doc:
                if doc['Host_Age_Unit'] is not None and doc['age'] is not None:
                    doc['age'] = doc['age'] + doc['Host_Age_Unit'].strip().lower()
                del doc['Host_Age_Unit']
            elif doc['age'] is not None:
                doc['age'] += 'y'
        return doc

    def define_strain_fixes(self):
        '''

        '''
        reader = csv.DictReader(filter(lambda row: row[0]!='#', open("source-data/strain_name_fix.tsv")), delimiter='\t')
        self.label_to_fix = {}
        for line in reader:
            self.label_to_fix[line['label'].decode('unicode-escape').replace(' ', '').lower()] = line['fix']
        reader = csv.DictReader(filter(lambda row: row[0]!='#', open("source-data/whole_strain_name_fix.tsv")), delimiter='\t')
        self.fix_whole_name = {}
        for line in reader:
            self.fix_whole_name[line['label'].decode('unicode-escape')] = line['fix']

    def fix_name(self, original_name, location):
        original_name = original_name.encode('ascii', 'replace')
        for gisaid_name, fixed_name in self.fix_whole_name.items():
            if original_name == gisaid_name:
                original_name = fixed_name

        name = original_name
        if '(' in name and ')' in name:  # A/Eskisehir/359/2016 (109) -> A/Eskisehir/359/2016 ; A/South Australia/55/2014  IVR145  (14/232) -> A/South Australia/55/2014  IVR145
            name = re.match(r'^([^(]+)', name).group(1)
        if 'clinical isolate' in name:  #B/clinical isolate SA116 Philippines/2002 -> B/Philippines/SA116/2002
            split_slash = name.split('/')
            split_space = split_slash[1].split(' ')
            name = "/".join([split_slash[0], split_space[3], split_space[2], split_slash[2]])
        if 'IRL' in name and '/' not in name: # 12IRL26168 -> A/Ireland/26168/2012  (All sequences with same pattern are H3N2)
            split_irl = name.split('IRL')
            name = "/".join(['A', 'Ireland', split_irl[1], "20" + split_irl[0]])
        if 'B/Victoria/2/87' in name:  # B/Finland/150/90 B/Victoria/2/1987 -> B/Finland/150/90
            result = name.split("B/Victoria/2/87")
            result = [x for x in result if x != '']  # Don't want to remove original B/Victoria/2/1987 strain
            if len(result) == 1:
                name = result[0]
        if '/USA/' in name and location is not None:    # A/Usa/AF1036/2007 , North America / United States / Colorado -> A/Colarado/AF1036/2007
            split_name = name.split('/')
            split_name[1] = location.split('/')[len(location.split('/')) - 1].strip()
            name = "/".join(split_name)
        name = name.replace('H1N1', '').replace('H5N6', '').replace('H3N2', '').replace('Human', '')\
            .replace('human', '').replace('//', '/').replace('.', '').replace(',', '').replace('&', '').replace(' ', '')\
            .replace('\'', '').replace('(', '').replace(')', '')

        split_name = name.split('/')
        for index, label in enumerate(split_name):
            if label.replace(' ', '').lower() in self.label_to_fix:
                split_name[index] = self.label_to_fix[label.replace(' ', '').lower()]
        if len(split_name[len(split_name) - 1]) == 2:  # B/Florida/1/96 -> B/Florida/1/1996
            try:
                year = int(split_name[len(split_name) - 1])
                if year < 10:
                    split_name[len(split_name) - 1] = "200" + str(year)
                elif year < 66:
                    split_name[len(split_name) - 1] = "20" + str(year)
                else:
                    split_name[len(split_name) - 1] = "19" + str(year)
            except:
                pass
        if len(split_name) == 4:
            if split_name[1].isupper():
                split_name[1] = split_name[1].title()  # B/WAKAYAMA-C/2/2016 becomes B/Wakayama-C/2/2016
            split_name[2] = split_name[2].lstrip('0')  # A/Mali/013MOP/2015 becomes A/Mali/13MOP/2015
        result_name = '/'.join(split_name)
        return result_name, original_name

    def define_countries(self):
        '''
        open synonym to country dictionary
        Location is to the level of country of administrative division when available
        '''
        file = open("source-data/geo_synonyms3.tsv")

        reader = csv.DictReader(filter(lambda row: row[0]!='#', file), delimiter='\t')		# list of dicts
        self.label_to_country = {}
        self.label_to_division = {}
        self.label_to_location = {}
        for line in reader:
            self.label_to_country[line['label'].decode('unicode-escape').lower()] = line['country']
            self.label_to_division[line['label'].decode('unicode-escape').lower()] = line['division']
            self.label_to_location[line['label'].decode('unicode-escape').lower()] = line['location']

    def format_country(self, v):
        '''
        Label viruses with country based on strain name
        '''
        if 'gisaid_location' in v:  # just run for virus documents
            strain_name = v['strain']
            original_name = v['gisaid_strain']
            if '/' in strain_name:
                name = strain_name.split('/')[1]
                if any(place.lower() == name.lower() for place in ['SaoPaulo', 'SantaCatarina', 'Calarasi', 'England', 'Sc']):
                    name = v['gisaid_location'].split('/')[len(v['gisaid_location'].split('/'))-1].strip()
                    result = self.determine_location(name)
                    if result is None:
                        result = self.determine_location(strain_name.split('/')[1])
                else:
                    result = self.determine_location(name)
            else:
                result = None
            if result is not None:
                v['country'], v['division'], v['location'] = result
            else:
                print("couldn't parse country for ", strain_name, "gisaid location", v['gisaid_location'], original_name)
            if 'BuenosAires' in v['strain']:
                if 'Brazil' in 'gisaid_location':
                    v['country'] = 'Brazil'
                    v['division'] = 'Pernambuco'
            if 'SantaCruz' in v['strain']:
                if 'Bolivia' in 'gisaid_location':
                    v['country'] = 'Bolivia'
                    v['division'] = 'SantaCruz'
            if 'ChristChurch' in v['strain']:
                if 'Barbados' in 'gisaid_location':
                    v['country'] = 'Barbados'
                    v['division'] = 'ChristChurch'
            if 'SaintPetersburg' in v['strain']:
                if 'United States' in 'gisaid_location':
                    v['country'] = 'USA'
                    v['division'] = 'Florida'
            if 'Georgia' in v['strain']:
                if 'Asia' in 'gisaid_location':
                    v['country'] = 'Georgia'
                    v['division'] = 'Georgia'
                    v['lcoation'] = 'Georgia'

    def determine_location(self, name):
        try:
            label = re.match(r'^([^/]+)', name).group(1).lower()						# check first for whole geo match
            if label in self.label_to_country:
                return (self.label_to_country[label], self.label_to_division[label], self.label_to_location[label])
            else:
                label = re.match(r'^([^\-^\/]+)', name).group(1).lower()			# check for partial geo match A/CHIBA-C/61/2014
            if label in self.label_to_country:
                return (self.label_to_country[label], self.label_to_division[label], self.label_to_location[label])
            else:
                label = re.match(r'^([A-Z][a-z]+)[A-Z0-9]', name).group(1).lower()			# check for partial geo match
            if label in self.label_to_country:
                return (self.label_to_country[label], self.label_to_division[label], self.label_to_location[label])
        except:
            return None

    def determine_group_fields(self, v, **kwargs):
        '''
        Determine and assign genetic group fields
        '''
        # determine virus type from strain name
        if 'Subtype' in v and 'Lineage' in v:
            if v['Subtype'] is not None:
                temp_subtype = v['Subtype'].lower()
            else:
                temp_subtype = ''
            del v['Subtype']
            if v['Lineage'] is not None:
                temp_lineage = v['Lineage'].lower()
            else:
                temp_lineage = ''
            del v['Lineage']
            v['vtype'] = 'tbd'
            v['subtype'] = 'tbd'
            v['lineage'] = 'tbd'
            if (temp_subtype, temp_lineage) in self.patterns:  #look for pattern from GISAID fasta file
                match = self.patterns[(temp_subtype, temp_lineage)]
                v['vtype'] = match[0].lower()
                v['subtype'] = match[1].lower()
                v['lineage'] = match[2].lower()
            else:
                self.groups.add(str(temp_subtype) + ':' + str(temp_lineage))
                self.groups_unknown += 1
            return v

if __name__=="__main__":
    args = parser.parse_args()
    sequence_fasta_fields = {0: 'accession', 1: 'strain', 2: 'isolate_id', 3:'locus', 4: 'passage', 5: 'submitting_lab'}
    #              >>B/Austria/896531/2016  | EPI_ISL_206054 | 687738 | HA | Siat 1
    setattr(args, 'fasta_fields', sequence_fasta_fields)
    xls_fields_wanted = [('strain', 'Isolate_Name'), ('isolate_id', 'Isolate_Id'), ('collection_date', 'Collection_Date'),
                             ('host', 'Host'), ('Subtype', 'Subtype'), ('Lineage', 'Lineage'),
                             ('gisaid_location', 'Location'), ('originating_lab', 'Originating_Lab'), ('Host_Age', 'Host_Age'),
                             ('Host_Age_Unit', 'Host_Age_Unit'), ('gender', 'Host_Gender'), ('submission_date', 'Submission_Date')]
    setattr(args, 'xls_fields_wanted', xls_fields_wanted)
    if args.path is None:
        args.path = "vdb/data/" + args.virus + "/"
    if not os.path.isdir(args.path):
        os.makedirs(args.path)
    connVDB = flu_upload(**args.__dict__)
    connVDB.upload(**args.__dict__)