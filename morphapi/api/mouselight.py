from rich.progress import track
import pandas as pd
from collections import namedtuple

from bg_atlasapi import BrainGlobeAtlas

from morphapi.utils.webqueries import post_mouselight, mouselight_base_url
from morphapi.paths_manager import Paths
from morphapi.utils.data_io import is_any_item_in_list, flatten_list
from morphapi.api.neuromorphorg import NeuroMorpOrgAPI


"""
    Collections of functions to query http://ml-neuronbrowser.janelia.org/ and get data about either the status of the API, 
    the brain regions or the neurons available. 
    Queries are sent by sending POST requests to http://ml-neuronbrowser.janelia.org/graphql
    with a string query. 
"""

# ---------------------------------------------------------------------------- #
#                                  QUERY UTILS                                 #
# ---------------------------------------------------------------------------- #


def mouselight_api_info():
    """
        Get the number of cells available in the database
    """
    # Get info from the ML API
    url = mouselight_base_url + "graphql"

    query = """
            query {
                queryData {
                    totalCount
                    }
                }
            """
    res = post_mouselight(url, query=query)
    print(
        "{} neurons on MouseLight database. ".format(
            res["queryData"]["totalCount"]
        )
    )


def mouselight_get_brainregions():
    """
        Get metadata about the brain brain regions as they are known by Janelia's Mouse Light. 
        IDs and Names sometimes differ from Allen's CCF.
    """

    # Download metadata about brain regions from the ML API
    url = mouselight_base_url + "graphql"
    # query =  "systemSettings {apiVersion apiRelease neuronCount\}}"
    query = """
            query {
                brainAreas{
                    
                    acronym
                    name
                    id
                    atlasId
                    graphOrder
                    parentStructureId
                    structureIdPath
                }
            }
            """
    res = post_mouselight(url, query=query)["brainAreas"]

    # Clean up and turn into a dataframe
    keys = {k: [] for k in res[0].keys()}
    for r in res:
        for k in r.keys():
            keys[k].append(r[k])

    structures_data = pd.DataFrame.from_dict(keys)
    return structures_data


def mouselight_structures_identifiers():
    """
    When the data are downloaded as SWC, each node has a structure identifier ID to tell if it's soma, axon or dendrite.
    This function returns the ID number --> structure table. 
    """

    # Download the identifiers used in ML neurons tracers
    url = mouselight_base_url + "graphql"
    # query =  "systemSettings {apiVersion apiRelease neuronCount\}}"
    query = """
            query {
                structureIdentifiers{
                    id
                    name
                    value
                }
            }
        """
    res = post_mouselight(url, query=query)["structureIdentifiers"]

    keys = {k: [] for k in res[0].keys()}
    for r in res:
        for k in r.keys():
            keys[k].append(r[k])

    structures_identifiers = pd.DataFrame.from_dict(keys)
    return structures_identifiers


def make_query(filterby=None, filter_regions=None, invert=False):
    """
    Constructs the strings used to submit graphql queries to the mouse light api

    :param filterby: str, soma, axon on dendrite. Search by neurite structure (Default value = None)
    :param filter_regions:  list, tuple. list of strings. Acronyms of brain regions to use for query (Default value = None)
    :param invert:  If true the inverse of the query is return (i.e. the neurons NOT in a brain region) (Default value = False)

    """
    searchneurons = """
                queryTime
                totalCount
                
                neurons{
                tag
                id
                idNumber
                idString
                
                brainArea{
                    id
                    acronym
                    name
                    safeName
                    atlasId
                    aliases
                    structureIdPath
                }

                tracings{
                    soma{
                    x
                    y
                    z
                    radius
                    brainArea{
                        id
                        acronym
                    }
                    sampleNumber
                    parentNumber
                    
                    }
                
                id
                tracingStructure{
                    name
                    value
                    id
                }
                }
            }
    """

    if filterby is None or filterby == "soma":
        query = """
                    query {{
                        searchNeurons {{
                            {}
                        }}
                    }}
                    """.format(
            searchneurons
        )
    else:
        raise NotImplementedError("This feature is not available yet")
        # Get predicate type
        if filterby.lower() in ["axon", "axons", "end point", "branch point"]:
            predicateType = 1

        elif filterby.lower() in [
            "dendrite",
            "apical dendrite",
            "(basal) dendrite",
        ]:
            raise NotImplementedError
            filterby = "(basal) dendrite"
            predicateType = 2
        else:
            raise ValueError("invalid search by argument")

        # Get neuron structure id
        structures_identifiers = mouselight_structures_identifiers()
        structureid = str(
            structures_identifiers.loc[
                structures_identifiers.name == filterby
            ]["id"].values[0]
        )

        # Get brain regions ids
        brainregions = mouselight_get_brainregions()
        brainareaids = [
            str(brainregions.loc[brainregions.acronym == a]["id"].values[0])
            for a in filter_regions
        ]

        # Get inversion
        if invert:
            invert = "true"
        else:
            invert = "false"

        query = """
        query {{
            searchNeurons (
                context: {{
                scope: 6
                predicates: [{{
                    predicateType: {predicate}
                    tracingIdsOrDOIs: []
                    tracingIdsOrDOIsExactMatch: false
                    tracingStructureIds: []
                    amount: 0
                    nodeStructureIds: ['{structure}']
                    brainAreaIds: {brainarea}
                    invert: {invert}
                    composition: 1
                    }}]
                }}
            ) {{
                {base}
            }}
        }}
        """.format(
            predicate=predicateType,
            structure=str(structureid),
            brainarea=brainareaids,
            invert=invert,
            base=searchneurons,
        )

        query = query.replace("\\t", "").replace("'", '"')
    return query


# ---------------------------------------------------------------------------- #
#                                  MAIN CLASS                                  #
# ---------------------------------------------------------------------------- #


class MouseLightAPI(Paths):
    def __init__(self, base_dir=None, **kwargs):
        """
            Handles the download of neurons morphology data from the Mouse Light project

            :param base_dir: path to directory to use for saving data (default value None)
            :param kwargs: can be used to pass path to individual data folders. See morphapi/utils /paths_manager.py
        """
        Paths.__init__(self, base_dir=base_dir, **kwargs)

    def fetch_neurons_metadata(
        self, filterby=None, filter_regions=None, **kwargs
    ):
        """
        Download neurons metadata and data from the API. The downloaded metadata can be filtered to keep only
        the neurons whose soma is in a list of user selected brain regions.
        
        :param filterby: Accepted values: "soma". If it's "soma", neurons are kept only when their soma
                        is in the list of brain regions defined by filter_regions (Default value = None)
        :param filter_regions: List of brain regions acronyms. If filtering neurons, these specify the filter criteria. (Default value = None)
        :param **kwargs: 

        """
        # Download all metadata
        print("Querying MouseLight API...")
        url = mouselight_base_url + "graphql"
        query = make_query(
            filterby=filterby, filter_regions=filter_regions, **kwargs
        )

        res = post_mouselight(url, query=query)["searchNeurons"]
        print(
            "     ... fetched metadata for {} neurons in {}s".format(
                res["totalCount"], round(res["queryTime"] / 1000, 2)
            )
        )

        # Process neurons to clean up the results and make them easier to handle
        neurons = res["neurons"]
        node = namedtuple("node", "x y z r area_acronym sample_n parent_n")
        tracing_structure = namedtuple(
            "tracing_structure", "id name value named_id"
        )

        cleaned_nurons = []  # <- output is stored here
        for neuron in neurons:
            if neuron["brainArea"] is not None:
                brainArea_acronym = neuron["brainArea"]["acronym"]
                brainArea_id = neuron["brainArea"]["id"]
                brainArea_name = neuron["brainArea"]["name"]
                brainArea_safename = neuron["brainArea"]["safeName"]
                brainArea_atlasId = neuron["brainArea"]["atlasId"]
                brainArea_aliases = neuron["brainArea"]["aliases"]
                brainArea_structureIdPath = neuron["brainArea"][
                    "structureIdPath"
                ]
            else:
                brainArea_acronym = None
                brainArea_id = None
                brainArea_name = None
                brainArea_safename = None
                brainArea_atlasId = None
                brainArea_aliases = None
                brainArea_structureIdPath = None

            if len(neuron["tracings"]) > 1:
                dendrite = tracing_structure(
                    neuron["tracings"][1]["id"],
                    neuron["tracings"][1]["tracingStructure"]["name"],
                    neuron["tracings"][1]["tracingStructure"]["value"],
                    neuron["tracings"][1]["tracingStructure"]["id"],
                )
            else:
                dendrite = None

            clean_neuron = dict(
                brainArea_acronym=brainArea_acronym,
                brainArea_id=brainArea_id,
                brainArea_name=brainArea_name,
                brainArea_safename=brainArea_safename,
                brainArea_atlasId=brainArea_atlasId,
                brainArea_aliases=brainArea_aliases,
                brainArea_structureIdPath=brainArea_structureIdPath,
                id=neuron["id"],
                idNumber=neuron["idNumber"],
                idString=neuron["idString"],
                tag=neuron["tag"],
                soma=node(
                    neuron["tracings"][0]["soma"]["x"],
                    neuron["tracings"][0]["soma"]["y"],
                    neuron["tracings"][0]["soma"]["z"],
                    neuron["tracings"][0]["soma"]["radius"],
                    neuron["tracings"][0]["soma"]["brainArea"],
                    neuron["tracings"][0]["soma"]["sampleNumber"],
                    neuron["tracings"][0]["soma"]["parentNumber"],
                ),
                axon=tracing_structure(
                    neuron["tracings"][0]["id"],
                    neuron["tracings"][0]["tracingStructure"]["name"],
                    neuron["tracings"][0]["tracingStructure"]["value"],
                    neuron["tracings"][0]["tracingStructure"]["id"],
                ),
                dendrite=dendrite,
            )
            cleaned_nurons.append(clean_neuron)

        # Filter neurons to keep only those matching the search criteria
        if filterby is not None:
            if filter_regions is None:
                raise ValueError(
                    "If filtering neuron by region, you need to pass a list of filter regions to use"
                )

            # get brain globe atlas
            atlas = BrainGlobeAtlas("allen_mouse_25um")

            # Filter by soma
            if filterby == "soma":
                filtered_neurons = []
                for neuron in cleaned_nurons:
                    if neuron["brainArea_acronym"] is None:
                        continue

                    # get ancestors of neuron's regions
                    try:
                        neuron_region_ancestors = atlas.get_structure_ancestors(
                            neuron["brainArea_acronym"]
                        )
                    except KeyError:
                        # ignore if region is not found
                        continue

                    # If any of the ancestors are in the allowed regions, keep neuron.
                    if is_any_item_in_list(
                        filter_regions, neuron_region_ancestors
                    ):
                        filtered_neurons.append(neuron)
                print(
                    "	... selected {} neurons out of {}".format(
                        len(filtered_neurons), res["totalCount"]
                    )
                )

                neurons = filtered_neurons
            else:
                print(
                    "	... selected {} neurons out of {}".format(
                        len(cleaned_nurons), res["totalCount"]
                    )
                )
                neurons = cleaned_nurons
        else:
            neurons = cleaned_nurons

        return neurons

    def download_neurons(self, neurons_metadata, **kwargs):
        """
        Given a list of neurons metadata from self.fetch_neurons_metadata
        this funcition downloads the morphological data.
        The data are actually downloaded from neuromorpho.org
        
        :param neurons_metadata: list with metadata for neurons to download
        :returns: list of Neuron instances

        """
        if not isinstance(neurons_metadata, (list, tuple)):
            neurons_metadata = [neurons_metadata]

        nmapi = NeuroMorpOrgAPI()
        nmapi._version = "Source-Version"

        neurons = []
        print("Downloading neurons")

        for neuron in track(neurons_metadata):
            downloaded = nmapi.download_neurons(
                nmapi.get_neuron_by_name(neuron["idString"]),
                _name="mouselight_",
            )

            print(downloaded)
            neurons.append(downloaded)

        return flatten_list(neurons)
