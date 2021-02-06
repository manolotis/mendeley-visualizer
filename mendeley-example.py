from flask import Flask, redirect, render_template, request, session, send_from_directory
import yaml
import json
import numpy as np
import os

from mendeley import Mendeley
from mendeley.session import MendeleySession

# with open('./config.yml') as f:
with open('/home/manolotis/sandbox/mendeley-visualizer/config.yml') as f:
    config = yaml.load(f)

REDIRECT_URI = 'http://localhost:5000/oauth'

app = Flask(__name__)
app.debug = True
app.secret_key = config['clientSecret']

mendeley = Mendeley(config['clientId'], config['clientSecret'], REDIRECT_URI)


@app.route('/')
def home():
    if 'token' in session:
        return redirect('/listFolders')

    auth = mendeley.start_authorization_code_flow()
    session['state'] = auth.state

    return render_template('home.html', login_url=(auth.get_login_url()))


@app.route('/oauth')
def auth_return():
    # auth = mendeley.start_authorization_code_flow(state=session['state'])
    state = request.args.get('state')
    auth = mendeley.start_authorization_code_flow(state=state)
    mendeley_session = auth.authenticate(request.url)

    session.clear()
    session['token'] = mendeley_session.token

    return redirect('/listFolders')


@app.route('/listFolders')
def list_folders():
    if 'token' not in session:
        return redirect('/')

    mendeley_session = get_session_from_cookies()
    name = mendeley_session.profiles.me.display_name
    folders = _get_folders(mendeley_session)

    return render_template('folders.html', folders=folders, name=name)


@app.route('/graph-coauthors')
def graph_coauthors():
    if 'token' not in session:
        return redirect('/')

    mendeley_session = get_session_from_cookies()

    # get arguments
    folder_id = request.args.get('folder_id')
    min_author_count = int(request.args.get('min_author_count', default=1))
    skip_refresh = bool(
        request.args.get('skip_refresh', default=False))  # skip reloading everything from Mendeley and load old json

    folder_name = _get_folder_name(mendeley_session, folder_id)

    SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
    json_url = os.path.join(SITE_ROOT, "static", "authors.json")

    if skip_refresh:
        with open(json_url) as f:
            G = json.load(f)
        return render_template('graph-coauthors.html', G=G, folder_name=folder_name)

    docs = _get_documents_in_folder_and_subfolders(mendeley_session, folder_id, unique=True)
    author_counts_sort = _get_author_counts(docs)
    max_author_count = _get_max_author_count(author_counts_sort)


    G = _get_author_graph_json(docs, author_counts_sort, max_author_count, min_author_count)
    with open(json_url, 'w') as outfile:
        print("saving to ", outfile.name)
        json.dump(G, outfile)


    return render_template('graph-coauthors.html', G=G, folder_name=folder_name)


@app.route('/folder')
def show_documents_in_folder():
    if 'token' not in session:
        return redirect('/')

    mendeley_session = get_session_from_cookies()
    folder_id = request.args.get('folder_id')
    folder_name = _get_folder_name(mendeley_session, folder_id)
    docs = _get_documents_in_folder_and_subfolders(mendeley_session, folder_id, unique=True)
    print(docs)

    return render_template('library.html', docs=docs, folder_name=folder_name)


@app.route('/metadataLookup')
def metadata_lookup():
    if 'token' not in session:
        return redirect('/')

    mendeley_session = get_session_from_cookies()

    doi = request.args.get('doi')
    doc = mendeley_session.catalog.by_identifier(doi=doi)

    return render_template('metadata.html', doc=doc)


@app.route('/download')
def download():
    if 'token' not in session:
        return redirect('/')

    mendeley_session = get_session_from_cookies()

    document_id = request.args.get('document_id')
    doc = mendeley_session.documents.get(document_id)
    doc_file = doc.files.list().items[0]

    return redirect(doc_file.download_url)


@app.route('/logout')
def logout():
    session.pop('token', None)
    return redirect('/')


@app.route('/document')
def get_document():
    if 'token' not in session:
        return redirect('/')

    mendeley_session = get_session_from_cookies()

    document_id = request.args.get('document_id')
    doc = mendeley_session.documents.get(document_id)

    return render_template('metadata.html', doc=doc)


@app.route('/static/<path:path>')
def serve(path):
    return send_from_directory('static', path)


def _get_folders(mendeley_session):
    folders = mendeley_session.request("GET", "https://api.mendeley.com/folders").json()
    return folders


def _get_children_folders(mendeley_session, parent_id, include_parent=False):
    folders = _get_folders(mendeley_session)
    added_folders = set()
    subfolders = []

    for folder in folders:
        print("checking folder: ", folder)
        try:
            if include_parent and folder["id"] == parent_id:  # todo: review, could introduce duplicates
                subfolders.append(folder)

            if folder["parent_id"] == parent_id:
                subfolders.append(folder)
                print("successfully added children folder")
            else:
                print("was not children folder")
        except KeyError:  # no parent_id --> top level folder
            print("entered exept, bc of KeyError")
            if include_parent and folder["id"] == parent_id:
                subfolders.append(folder)
                print("added to subfolder")
            else:
                print("not added")
    return subfolders


def _get_documents_in_folder(mendeley_session, folder_id):
    url = "https://api.mendeley.com/folders/{}/documents?limit=500".format(folder_id)
    doc_ids = mendeley_session.request('GET', url).json()

    docs = [mendeley_session.documents.get(doc_id['id']) for doc_id in doc_ids]

    return docs


def _get_author_counts(docs, sort=True):
    author_counts = {}

    for doc in docs:
        for author in doc.authors:
            author_name = "{} {}".format(author.first_name, author.last_name)
            if author_name not in author_counts:
                author_counts[author_name] = 1
            else:
                author_counts[author_name] += 1

    if not sort:
        return author_counts

    author_counts_sort = dict(sorted(author_counts.items(), key=lambda item: item[1], reverse=True))
    return author_counts_sort


def _get_unique_docs(docs):
    unique_docs = []
    added_doc_ids = set()

    for doc in docs:
        print(doc)
        if doc.id not in added_doc_ids:
            added_doc_ids.add(doc.id)
            unique_docs.append(doc)

    return unique_docs


def _get_folder_name(mendeley_session, folder_id):
    folders = _get_folders(mendeley_session)
    folder_name = ''
    for folder in folders:
        if folder['id'] == folder_id:
            print("found the folder, name: ", folder['name'])
            folder_name = folder['name']
    return folder_name


def _get_max_author_count(author_counts):
    counts = []
    authors = []
    for a, c in author_counts.items():
        authors.append(a)
        counts.append(c)
    max_author_count = max(counts)
    return max_author_count


def _get_documents_in_folder_and_subfolders(mendeley_session, folder_id, unique=True):
    folder_and_subfolders = _get_children_folders(mendeley_session, folder_id, include_parent=True)

    docs = []
    for folder in folder_and_subfolders:
        docs.extend(_get_documents_in_folder(mendeley_session, folder["id"]))

    if unique:
        return _get_unique_docs(docs)

    return docs


def _get_author_graph_json(docs, author_counts, max_count, min_author_count):
    print("min author count", min_author_count)
    nodes = _get_author_nodes(author_counts, max_count, min_author_count)
    links = _get_author_links(docs, nodes)

    G = {
        "nodes": nodes,
        "links": links
    }

    return G


def _get_author_nodes(author_counts, max_count, min_author_count=1):
    nodes = []

    for author, count in author_counts.items():
        if count < min_author_count:
            continue
        node = {
            "id": author,
            "group": count,
            "size": 3 * count,
            "score": np.round(1.0 * count / max_count, decimals=3)
        }
        nodes.append(node)

    return nodes


def _get_author2index(nodes):
    author2index = {}
    for i, node in enumerate(nodes):
        author2index[node['id']] = i
    print("author2index: ", author2index)
    print("nodes: ", nodes)
    return author2index


def _get_author_links(docs, nodes):
    links = []
    author2index = _get_author2index(nodes)
    added_links = set()

    for doc in docs:
        for author in doc.authors:
            author_name = "{} {}".format(author.first_name, author.last_name)
            other_authors = [a for a in doc.authors if a != author]
            for other_author in other_authors:
                other_author_name = "{} {}".format(other_author.first_name, other_author.last_name)
                try:
                    source, target = author2index[author_name], author2index[other_author_name]
                    # source, target = author_name, other_author_name
                except KeyError:
                    print("skipping {} due to key error!".format(other_author_name))
                    continue
                link = {
                    "source": source,
                    "target": target,
                    "value": 1,  # todo: update
                }
                if (source, target) not in added_links and (target, source) not in added_links and source != target:
                    added_links.add((source, target))
                    links.append(link)

    return links


def get_session_from_cookies():
    return MendeleySession(mendeley, session['token'])


if __name__ == '__main__':
    app.run()

# Old stuff. Left for referece purposes
#
# @app.route('/listDocuments')
# def list_documents():
#     if 'token' not in session:
#         return redirect('/')
#
#     mendeley_session = get_session_from_cookies()
#     name = mendeley_session.profiles.me.display_name
#     docs = mendeley_session.documents.list(view='client').items
#
#     return render_template('library.html', name=name, docs=docs)
