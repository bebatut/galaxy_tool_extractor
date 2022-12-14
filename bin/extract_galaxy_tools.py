#!/usr/bin/env python

import argparse
import base64
from unicodedata import category
import pandas as pd
import requests
import time
import yaml
import xml.etree.ElementTree as et

from github import Github
from pathlib import Path


def read_file(filepath):
    '''
    Read an optional file with 1 element per line

    :param filepath: path to a file
    '''
    if filepath is None:
        return []
    fp = Path(filepath)
    if fp.is_file():
        with fp.open('r') as f:
            return [x.rstrip() for x in f.readlines()]
    else:
        return []


def get_string_content(cf):
    '''
    Get string of the content from a ContentFile

    :param cf: GitHub ContentFile object
    '''
    return base64.b64decode(cf.content).decode('utf-8')


def get_tool_github_repositories(g):
    '''
    Get list of tool GitHub repositories to parse

    :param g: GitHub instance
    '''
    repo = g.get_user("galaxyproject").get_repo("planemo-monitor")
    repo_list = []
    for i in range(1, 5):
        repo_f = repo.get_contents(f"repositories0{i}.list")
        repo_l = get_string_content(repo_f).rstrip()
        repo_list += repo_l.split("\n")
    return(repo_list)


def get_github_repo(url, g):
    '''
    Get a GitHub Repository object from an URL

    :param url: URL to a GitHub repository
    :param g: GitHub instance
    '''
    if not url.startswith("https://github.com/"):
        raise ValueError
    if url.endswith("/"):
        url = url[:-1]
    if url.endswith(".git"):
        url = url[:-4]
    u_split = url.split("/")
    return g.get_user(u_split[-2]).get_repo(u_split[-1])


def get_shed_attribute(attrib, shed_content, empty_value):
    '''
    Get a shed attribute

    :param attrib: attribute to extract
    :param shed_content: content of the .shed.yml
    :param empty_value: value to return if attribute not found
    '''
    if attrib in shed_content:
        return shed_content[attrib]
    else:
        return empty_value


def get_biotools(el):
    '''
    Get bio.tools information

    :param el: Element object
    '''
    xrefs = el.find('xrefs')
    if xrefs is not None:
        xref = xrefs.find('xref')
        if xref is not None and xref.attrib['type'] == 'bio.tools':
            return xref.text
    return None


def get_conda_package(el):
    '''
    Get conda package information

    :param el: Element object
    '''
    reqs = el.find('requirements')
    if reqs is not None:
        req = reqs.find('requirement')
        if req is not None:
            return req.text
        #for req in reqs.findall('requirement'):
        #    if 'version' in req.attrib:
        #        if req.attrib['version'] == '@VERSION@' or req.attrib['version'] == '@TOOL_VERSION@':
        #            return req.text
        #        elif req.attrib['version']
        #    elif 'version' in req.attrib:
        #        return req.text
        #    else:
        #        return req.text
    return None


def check_categories(ts_categories, ts_cat):
    '''
    Check if tool fit in ToolShed categories to keep

    :param ts_categories: tool ToolShed categories
    :param ts_cat: list of ToolShed categories to keep in the extraction
    '''
    if ts_categories is not None and len(ts_cat) > 0:
        to_keep = False
        for cat in ts_categories:
            if cat in ts_cat:
                to_keep = True
        return to_keep
    return True

def get_tool_metadata(tool, repo, ts_cat, excluded_tools):
    '''
    Get tool information
    - Check the `.shed.yaml` file
    - Extract metadata from the `.shed.yaml`
    - Filter for specific ToolShed categories
    - Extract the requirements in the macros or xml file to get tool version supported in Galaxy
    - Extract bio.tools information if available in the macros or xml

    :param tool: GitHub ContentFile object
    :param repo: GitHub Repository object
    :param ts_cat: list of ToolShed categories to keep in the extraction
    :param excluded_tools: list of tools to skip
    '''
    if tool.type != 'dir':
        return None
    # avoid extracting information from excluded tools
    if tool.name in excluded_tools:
        return None
    print(tool.name)
    metadata = {
        'Galaxy wrapper id': tool.name,
        'Description': None,
        'bio.tool id': None,
        'bio.tool name': None,
        'bio.tool description': None,
        'EDAM operation': [],
        'EDAM topic': [],
        'Status': "To update",
        'Source': None,
        'ToolShed categories': [],
        'ToolShed id': None,
        'Galaxy wrapper owner': None,
        'Galaxy wrapper source': None,
        'Galaxy wrapper version': None,
        'bio.tool id': None,
        'Conda id': None,
        'Conda version': None
    }
    # extract .shed.yml information and check macros.xml
    try:
        shed = repo.get_contents(f"{tool.path}/.shed.yml")
    except:
        return None
    else:
        file_content = get_string_content(shed)
        yaml_content = yaml.load(file_content, Loader=yaml.FullLoader)
        metadata['Description'] = get_shed_attribute('description', yaml_content, None)
        if metadata['Description'] is None:
            metadata['Description'] = get_shed_attribute('long_description', yaml_content, None)
        if metadata['Description'] is not None:
            metadata['Description'] = metadata['Description'].replace("\n","")
        metadata['ToolShed id'] = get_shed_attribute('name', yaml_content, None)
        metadata['Galaxy wrapper owner'] = get_shed_attribute('owner', yaml_content, None)
        metadata['Galaxy wrapper source'] = get_shed_attribute('remote_repository_url', yaml_content, None)
        if 'homepage_url' in yaml_content:
            metadata['Source'] = yaml_content['homepage_url']
        metadata['ToolShed categories'] = get_shed_attribute('categories', yaml_content, [])
        if metadata['ToolShed categories'] is None:
            metadata['ToolShed categories'] = []
    # filter ToolShed categories and leave function if not in expected categories
    if not check_categories(metadata['ToolShed categories'], ts_cat):
        return None
    # find and parse macro file
    for file in repo.get_contents(tool.path):
        if 'macro' in file.name and file.name.endswith('xml'):
            file_content = get_string_content(file)
            root = et.fromstring(file_content)
            for child in root:
                if 'name' in child.attrib:
                    if child.attrib['name'] == '@TOOL_VERSION@' or child.attrib['name'] == '@VERSION@':
                        metadata['Galaxy wrapper version'] = child.text
                    elif child.attrib['name'] == 'bio_tools':
                        metadata['bio.tool id'] = get_biotools(child)
                    elif child.attrib['name'] == 'requirements':
                        metadata['Conda id'] = get_conda_package(child)
    # parse XML file if metadata not found in the macro file
    if metadata['Galaxy wrapper version'] is None or metadata['bio.tool id'] is None or metadata['Conda id'] is None:
        for file in repo.get_contents(tool.path):
            if file.name.endswith('xml') and 'macro' not in file.name:
                file_content = get_string_content(file)
                root = et.fromstring(file_content)
                # version
                if metadata['Galaxy wrapper version'] is None:
                    if 'version' in root.attrib:
                        version = root.attrib['version']
                        if 'VERSION@' not in version:
                            metadata['Galaxy wrapper version'] = version
                        else:
                            macros = root.find('macros')
                            if macros is not None:
                                for child in macros:
                                    if 'name' in child.attrib and (child.attrib['name'] == '@TOOL_VERSION@' or child.attrib['name'] == '@VERSION@'):
                                        metadata['Galaxy wrapper version'] = child.text
                # bio.tools
                if metadata['bio.tool id'] is None:
                    biotools = get_biotools(root)
                    if biotools is not None:
                        metadata['bio.tool id'] = biotools
                # conda package
                if metadata['Conda id'] is None:
                    reqs = get_conda_package(root)
                    if reqs is not None:
                        metadata['Conda id'] = reqs
    # get latest conda version and compare to the wrapper version
    if metadata["Conda id"] is not None:
        r = requests.get(f'https://api.anaconda.org/package/bioconda/{metadata["Conda id"]}')
        if r.status_code == requests.codes.ok:
            conda_info = r.json()
            if "latest_version" in conda_info:
                metadata['Conda version'] = conda_info['latest_version']
                if metadata['Conda version'] == metadata['Galaxy wrapper version']:
                    metadata['Status'] = 'Up-to-date'
    # get bio.tool information
    if metadata["bio.tool id"] is not None:
        r = requests.get(f'https://bio.tools/api/tool/{metadata["bio.tool id"]}/?format=json')
        if r.status_code == requests.codes.ok:
            biotool_info = r.json()
            if "function" in biotool_info and 'operation' in biotool_info['function']:
                for op in biotool_info['function']['operation']:
                    metadata['EDAM operation'].append(op['term'])
            if "topic" in biotool_info:
                for t in biotool_info['topic']:
                    metadata['EDAM topic'].append(t['term'])
            if "name" in biotool_info:
                metadata['bio.tool name'] = biotool_info['name']
            if "description" in biotool_info:
                metadata['bio.tool description'] = biotool_info['description'].replace("\n","")
    return metadata


def parse_tools(repo, ts_cat=[], excluded_tools=[]):
    '''
    Parse tools in a GitHub repository to expact

    :param repo: GitHub Repository object
    :param ts_cat: list of ToolShed categories to keep in the extraction
    :param excluded_tools: list of tools to skip
    '''
    try:
        repo_tools = repo.get_contents("tools")
    except:
        try:
            repo_tools = repo.get_contents("wrappers")
        except:
            print("No tool folder found")
            return []
    tools = []
    for tool in repo_tools:
        # to avoid API request limit issue, wait for one hour
        if g.get_rate_limit().core.remaining < 200:
            print("WAITING for 1 hour to retrieve GitHub API request access !!!")
            print()
            time.sleep(60*60)
        # parse tool
        try:
            shed = repo.get_contents(f"{tool.path}/.shed.yml")
        except:
            if tool.type != 'dir':
                continue
            for content in repo.get_contents(tool.path):
                metadata = get_tool_metadata(content, repo, ts_cat, excluded_tools)
                if metadata is not None:
                    tools.append(metadata)
        else:
            metadata = get_tool_metadata(tool, repo, ts_cat, excluded_tools)
            if metadata is not None:
                tools.append(metadata)
    return tools

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract a GitHub project to CSV')
    parser.add_argument('--api', '-a', required=True, help="GitHub access token")
    parser.add_argument('--output', '-o', required=True, help="Output filepath")
    parser.add_argument('--categories', '-c', help="Path to a file with ToolShed category to keep in the extraction (one per line)")
    parser.add_argument('--excluded', '-e', help="Path to a file with ToolShed ids of tools to exclude (one per line)")
    args = parser.parse_args()

    # connect to GitHub
    g = Github(args.api)
    # get list of GitHub repositories to parse
    repo_list = get_tool_github_repositories(g)

    # get categories and tools to exclude
    categories = read_file(args.categories)
    excl_tools = read_file(args.excluded)

    # parse tools in GitHub repositories to extract metada and filter by TS categories
    tools = []
    for r in repo_list:
        print(r)
        if "github" not in r:
            continue
        repo = get_github_repo(r, g)
        tools += parse_tools(repo, categories, excl_tools)
        print()

    # export tool metadata to tsv output file
    df = pd.DataFrame(tools)
    df['ToolShed categories'] = df['ToolShed categories'].apply(lambda x: ', '.join([str(i) for i in x]))
    df['EDAM operation'] = df['EDAM operation'].apply(lambda x: ', '.join([str(i) for i in x]))
    df['EDAM topic'] = df['EDAM topic'].apply(lambda x: ', '.join([str(i) for i in x]))
    df.to_csv(args.output, sep="\t", index=False)
