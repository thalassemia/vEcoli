"""
BuildNetwork
============

Constructs a network representations of simulation components from sim_data,
and generates files for node lists and edge lists.

Adding new nodes to the network:
--------------------------------

To add a new type of nodes to the network (either a state or process), you need
to write a new function within this file (build_network.py), which goes through
all of the instances of the new node type, and for each instance creates a
node:

.. code-block:: python

    new_node = Node()

adds attributes (``**attr``), which include "node_class", "node_type", "node_id",
"name", and "synonyms":

.. code-block:: python

    new_node.read_attributes(**attr)

and appends the node to the node list:

.. code-block:: python

    self.node_list.append(new_node)

The relevant edges that connect the new node to other nodes also need to be
specified:

.. code-block:: python

    new_edge = Edge("Edge Type")

The source and destination ids for that edge are added with an attribute:

.. code-block:: python

    attr = {
            'src_id': source_id,
            'dst_id': destination_id,
            }

    new_edge.read_attributes(**attr)

and the edge is then added to the edge list:

.. code-block:: python

    self.edge_list.append(new_edge)

With a complete node and edge list, you are ready to add dynamics data to each
node. This is done in read_dynamics.py. You first need to choose appropriate
dynamics data to represents that node's activity, and make sure it is saved in
a listener. read_dynamics.py uses saved listener output to load dynamics into
each node.

read_dynamics.py might require a new function to the dynamics data if it is of
a new node type, specified in the TYPE_TO_READER_FUNCTION dictionary. When the
node list is read, nodes of the new type will be passed into the new function,
which assigns that node dynamics from listener output:

.. code-block:: python

    node.read_dynamics(dynamics, dynamics_units)
"""

from collections import Counter
import numpy as np
import re
import os
import pickle
import json
from typing import Optional


from ecoli.analysis.causality_network.network_components import (
    Node,
    Edge,
    NODELIST_FILENAME,
    EDGELIST_FILENAME,
    NODE_LIST_HEADER,
    EDGE_LIST_HEADER,
    NODELIST_JSON,
    EDGELIST_JSON,
)

# Suffixes that are added to the node IDs of a particular type of node
NODE_ID_SUFFIX = {
    "transcription": "_TRS",
    "maturation": "_MAT",
    "translation": "_TRL",
    "regulation": "_REG",
}

# URL template
URL_TEMPLATE = (
    "https://ecocyc.org/ECOLI/substring-search?type=NIL&object={0}&"
    "quickSearch=Quick+Search"
)
URL_TEMPLATE_COMPOUND = "https://ecocyc.org/compound?orgid=ECOLI&id={0}"

"""
The following groups of molecules participate in multiple processes and are
thus identified here to prevent the addition of duplicate nodes.

Note:
	Identifying multi-process participatory molecules in this way is not
	required because --check_sanity checks for duplicate nodes. However,
	identifying such molecules here can streamline network building by
	eliminating the need to search through nodes that were added previously.

TODO:
	Future proof by programmatically finding multi-process participatory
	molecules (maybe dictionary of booleans).
"""
# Proteins that are reactants or products of a metabolic reaction
PROTEINS_IN_METABOLISM = [
    "EG50003-MONOMER[c]",
    "PHOB-MONOMER[c]",
    "PTSI-MONOMER[c]",
    "PTSH-MONOMER[c]",
]

# Equilibrium complexes that are formed from deactivated equilibrium reactions,
# but are reactants in a complexation reaction
EQUILIBRIUM_COMPLEXES_IN_COMPLEXATION = [
    "CPLX0-7701[c]",
    "CPLX0-7677[c]",
    "MONOMER0-1781[c]",
    "CPLX0-7702[c]",
]

# Metabolites that are used as ligands in equilibrium, but do not participate
# in any metabolic reactions
METABOLITES_ONLY_IN_EQUILIBRIUM = ["CPD-7[c]"]

# Molecules in 2CS (two component system) reactions that are not proteins
NONPROTEIN_MOLECULES_IN_2CS = [
    "ATP[c]",
    "ADP[c]",
    "WATER[c]",
    "Pi[c]",
    "PROTON[c]",
    "PHOSPHO-PHOB[c]",
]

COMPARTMENTS = {
    "n": "nucleoid",
    "j": "projection",
    "w": "cell wall",
    "c": "cytoplasm",
    "e": "extracellular",
    "m": "membrane",
    "o": "outer membrane",
    "p": "periplasm",
    "l": "pilus",
    "i": "inner membrane",
    "s": "flagellum",
}


def molecule_compartment(molecule):
    match = re.match(r".+\[(.)]$", molecule)
    if match:
        return COMPARTMENTS.get(match.groups()[0])


class BuildNetwork(object):
    """
    Constructs a causality network of simulation components, namely states and
    processes, of a whole-cell simulation using sim_data. Writes two files
    (node list and edge list) that are subsequently used by the dynamics reader
    to extract simulation results, and for the visual representation of the
    network.
    """

    def __init__(self, sim_data_file, output_dir, check_sanity=False):
        """
        TODO: have check_sanity looks for disconnected nodes, and edges
        with non-existent nodes.

        Args:
            sim_data_file: path to the variant sim_data pickle file used for
                building the network.
            output_dir: output directory for the node list and edge list files.
            check_sanity: if set to True, checks if there are any nodes with
                duplicate IDs in the network.
        """
        # Open simulation data and save as attribute
        with open(sim_data_file, "rb") as f:
            self.sim_data = pickle.load(f)

        self.output_dir = output_dir
        self.check_sanity = check_sanity

        self.common_names = self.sim_data.common_names

        self.node_list = []
        self.edge_list = []

    def run(self):
        """
        Build the network and write node/edge list files.
        """
        self._build_network()
        self._write_json()
        # self._write_files()

    def build_nodes_and_edges(self):
        """Build the network and return the node and edge lists."""
        self._build_network()
        return self._node_list(), self._edge_list()

    def _build_network(self):
        """
        Add nodes and edges to the node/edge lists, and check for network
        sanity (optional).
        """
        # Add global nodes to the node list
        self._add_global_nodes()

        # Add state/process-specific nodes and edges to the node and edge list
        self._add_genes()
        self._add_transcription_and_transcripts()
        self._add_rna_maturation_and_mature_transcripts()
        self._add_translation_and_monomers()
        self._add_complexation_and_complexes()
        self._add_metabolism_and_metabolites()
        self._add_equilibrium()
        self._add_regulation()
        self._remove_hanging_edges()

        # Check for network sanity (optional)
        if self.check_sanity:
            self._find_duplicate_nodes()

    def _node_list(self):
        return [node.to_dict() for node in self.node_list]

    def _edge_list(self):
        def edge_dict(edge):
            return {
                "src_node_id": edge.src_id,
                "dst_node_id": edge.dst_id,
                "stoichiometry": edge.stoichiometry,
                "process": edge.process,
            }

        return [edge_dict(edge) for edge in self.edge_list]

    def _write_files(self):
        """
        Write node/edge list as separate .tsv files.
        """
        # Open node list file
        with open(
            os.path.join(self.output_dir, NODELIST_FILENAME), "w"
        ) as nodelist_file:
            # Write header row
            nodelist_file.write(NODE_LIST_HEADER + "\n")

            # Write one row for each node
            for node in self.node_list:
                node.write_nodelist(nodelist_file)

        # Open edge list file
        with open(
            os.path.join(self.output_dir, EDGELIST_FILENAME), "w"
        ) as edgelist_file:
            # Write header row
            edgelist_file.write(EDGE_LIST_HEADER + "\n")

            # Write one row for each edge
            for edge in self.edge_list:
                edge.write_edgelist(edgelist_file)

    def _write_json(self):
        """Write node and edge lists as json files."""
        nodes = self._node_list()
        node_json = json.dumps(nodes)
        node_path = os.path.join(self.output_dir, NODELIST_JSON)
        print("writing {} nodes to node file {}".format(len(nodes), node_path))
        with open(node_path, "w") as node_file:
            node_file.write(node_json)

        edges = self._edge_list()
        edge_json = json.dumps(edges)
        edge_path = os.path.join(self.output_dir, EDGELIST_JSON)
        print("writing {} edges to edge file {}".format(len(edges), edge_path))
        with open(edge_path, "w") as edge_file:
            edge_file.write(edge_json)

    def _add_global_nodes(self):
        """
        Add global state nodes to the node list.
        """
        # Add total cell mass node to node list
        mass_node = Node()
        attr = {
            "node_class": "State",
            "node_type": "Global",
            "node_id": "cell_mass",
            "name": "Total cell mass",
        }
        mass_node.read_attributes(**attr)

        # Add total cell volume node to node list
        volume_node = Node()
        attr = {
            "node_class": "State",
            "node_type": "Global",
            "node_id": "cell_volume",
            "name": "Total cell volume",
        }
        volume_node.read_attributes(**attr)

        self.node_list.extend([mass_node, volume_node])

    def _add_genes(self):
        """
        Add gene state nodes to the node list.
        """
        # Loop through all genes (in the order listed in transcription)
        for gene_id in self.sim_data.process.transcription.cistron_data["gene_id"]:
            # Initialize a single gene node
            gene_node = Node()

            # Get name and synonyms for gene
            gene_name = self.common_names.get_common_name(gene_id)
            gene_synonyms = self.common_names.get_synonyms(gene_id)

            # Get URL for gene
            gene_url = URL_TEMPLATE.format(gene_id)

            attr = {
                "node_class": "State",
                "node_type": "Gene",
                "node_id": gene_id,
                "name": gene_name,
                "synonyms": gene_synonyms,
                "url": gene_url,
                "location": COMPARTMENTS["n"],
            }

            gene_node.read_attributes(**attr)

            # Append gene node to node_list
            self.node_list.append(gene_node)

    def _add_transcription_and_transcripts(self):
        """
        Add transcription process nodes and transcript state nodes to the node
        list, and edges connected to the transcription nodes to the edge list.
        """
        ntp_ids = self.sim_data.molecule_groups.ntps
        ppi_id = self.sim_data.molecule_ids.ppi
        rnap_id = self.sim_data.molecule_ids.full_RNAP
        all_gene_ids = self.sim_data.process.transcription.cistron_data["gene_id"]

        # Loop through all transcripts (in the order listed in transcription)
        for rna_id in self.sim_data.process.transcription.rna_data["id"]:
            # Initialize a single transcript node
            rna_node = Node()

            # Add attributes to the node
            # Add common name and synonyms
            rna_id_no_compartment = rna_id[:-3]
            rna_name = self.common_names.get_common_name(rna_id_no_compartment)
            rna_synonyms = self.common_names.get_synonyms(rna_id_no_compartment)

            attr = {
                "node_class": "State",
                "node_type": "RNA",
                "node_id": rna_id,
                "name": rna_name,
                "synonyms": rna_synonyms,
                "location": molecule_compartment(rna_id),
            }

            rna_node.read_attributes(**attr)

            # Append transcript node to node_list
            self.node_list.append(rna_node)

            # Initialize a single transcription node for each TU
            transcription_node = Node()

            # Add attributes to the node
            transcription_id = rna_id_no_compartment + NODE_ID_SUFFIX["transcription"]
            transcription_name = rna_name + " transcription"
            if isinstance(rna_synonyms, list):
                transcription_synonyms = [x + " transcription" for x in rna_synonyms]
            else:
                transcription_synonyms = [rna_synonyms]

            attr = {
                "node_class": "Process",
                "node_type": "Transcription",
                "node_id": transcription_id,
                "name": transcription_name,
                "synonyms": transcription_synonyms,
                "location": molecule_compartment(transcription_id),
            }
            transcription_node.read_attributes(**attr)

            # Append transcription node to node_list
            self.node_list.append(transcription_node)

            # Get IDs of genes that constitute the transcript
            constituent_gene_ids = [
                all_gene_ids[i]
                for i in self.sim_data.process.transcription.rna_id_to_cistron_indexes(
                    rna_id
                )
            ]

            # Add edge from gene to transcription node
            for gene_id in constituent_gene_ids:
                self._append_edge("Transcription", gene_id, transcription_id)

            # Add edge from transcription to transcript node
            self._append_edge("Transcription", transcription_id, rna_id)

            # Add edges from NTPs to transcription nodes
            for ntp_id in ntp_ids:
                self._append_edge("Transcription", ntp_id, transcription_id)

            # Add edge from transcription to Ppi
            self._append_edge("Transcription", transcription_id, ppi_id)

            # Add edges from RNA polymerases to transcription
            self._append_edge("Transcription", rnap_id, transcription_id)

    def _add_rna_maturation_and_mature_transcripts(self):
        """
        Add RNA maturation process nodes and mature transcript state nodes to
        the node list, and edges connected to these nodes to the edge list.
        """
        transcription = self.sim_data.process.transcription
        mature_rna_ids = transcription.mature_rna_data["id"]
        unprocessed_rna_ids = transcription.rna_data["id"][
            transcription.rna_data["is_unprocessed"]
        ]
        rna_maturation_stoich_matrix = transcription.rna_maturation_stoich_matrix
        rna_maturation_enzyme_matrix = transcription.rna_maturation_enzyme_matrix
        rna_maturation_enzymes = transcription.rna_maturation_enzymes
        ntp_ids = self.sim_data.molecule_groups.ntps
        ppi_id = self.sim_data.molecule_ids.ppi

        # Loop through all mature transcripts (in the order listed in
        # transcription)
        for mature_rna_id in mature_rna_ids:
            # Initialize a single transcript node
            rna_node = Node()

            # Add attributes to the node
            # Add common name and synonyms
            rna_id_no_compartment = mature_rna_id[:-3]
            rna_name = self.common_names.get_common_name(rna_id_no_compartment)
            rna_synonyms = self.common_names.get_synonyms(rna_id_no_compartment)

            attr = {
                "node_class": "State",
                "node_type": "RNA",
                "node_id": mature_rna_id,
                "name": rna_name,
                "synonyms": rna_synonyms,
                "location": molecule_compartment(mature_rna_id),
            }

            rna_node.read_attributes(**attr)

            # Append transcript node to node_list
            self.node_list.append(rna_node)

        for i, unprocessed_rna_id in enumerate(unprocessed_rna_ids):
            # Initialize a single RNA maturation node for each unprocessed transcipt
            rna_maturation_node = Node()

            # Add attributes to the node
            rna_id_no_compartment = unprocessed_rna_id[:-3]
            rna_name = self.common_names.get_common_name(rna_id_no_compartment)
            rna_synonyms = self.common_names.get_synonyms(rna_id_no_compartment)

            maturation_id = rna_id_no_compartment + NODE_ID_SUFFIX["maturation"]
            maturation_name = rna_name + " maturation"
            if isinstance(rna_synonyms, list):
                maturation_synonyms = [x + " maturation" for x in rna_synonyms]
            else:
                maturation_synonyms = [rna_synonyms]

            attr = {
                "node_class": "Process",
                "node_type": "RNA Maturation",
                "node_id": maturation_id,
                "name": maturation_name,
                "synonyms": maturation_synonyms,
            }
            rna_maturation_node.read_attributes(**attr)

            # Append transcription node to node_list
            self.node_list.append(rna_maturation_node)

            # Get IDs of mature RNAs that are generated from this transcript
            constituent_transcript_ids = [
                mature_rna_ids[i]
                for i in rna_maturation_stoich_matrix.getcol(i).nonzero()[0]
            ]

            # Add edge from the maturation node to mature transcripts
            for rna_id in constituent_transcript_ids:
                self._append_edge("RNA Maturation", maturation_id, rna_id)

            # Add edge from the unprocessed rna to the maturation node
            self._append_edge("RNA Maturation", unprocessed_rna_id, maturation_id)

            # Add edges from maturation node to NTPs
            for ntp_id in ntp_ids:
                self._append_edge("RNA Maturation", maturation_id, ntp_id)

            # Add edge from ppi to maturation node
            self._append_edge("RNA Maturation", ppi_id, maturation_id)

            # Get indexes of enzymes that catalyze the maturation
            enzyme_indexes = np.where(rna_maturation_enzyme_matrix[i, :])[0]

            # Add edge from each enzyme to maturation node
            for enzyme_index in enzyme_indexes:
                self._append_edge(
                    "RNA Maturation",
                    rna_maturation_enzymes[enzyme_index],
                    maturation_id,
                )

    def _add_translation_and_monomers(self):
        """
        Add translation process nodes and protein (monomer) state nodes to the
        node list, and edges connected to the translation nodes to the edge
        list.
        """
        # Create nodes for amino acids
        aa_ids = self.sim_data.molecule_groups.amino_acids
        gtp_id = "GTP[c]"
        gdp_id = "GDP[c]"
        water_id = self.sim_data.molecule_ids.water
        ppi_id = self.sim_data.molecule_ids.ppi

        ribosome_subunit_ids = [
            self.sim_data.molecule_ids.s30_full_complex,
            self.sim_data.molecule_ids.s50_full_complex,
        ]
        all_rna_ids = self.sim_data.process.transcription.rna_data["id"]

        # Construct dictionary to get corresponding gene IDs from RNA IDs
        rna_id_to_gene_id = {}
        for cistron_id, gene_id in zip(
            self.sim_data.process.transcription.cistron_data["id"],
            self.sim_data.process.transcription.cistron_data["gene_id"],
        ):
            rna_id_to_gene_id[cistron_id] = gene_id

        # Loop through all translatable genes
        for monomer_id, cistron_id in zip(
            self.sim_data.process.translation.monomer_data["id"],
            self.sim_data.process.translation.monomer_data["cistron_id"],
        ):
            gene_id = rna_id_to_gene_id[cistron_id]

            # Initialize a single protein node
            protein_node = Node()

            # Add attributes to the node
            monomer_id_no_compartment = monomer_id[:-3]
            monomer_name = self.common_names.get_common_name(monomer_id_no_compartment)
            monomer_synonyms = self.common_names.get_synonyms(monomer_id_no_compartment)
            gene_name = self.common_names.get_common_name(gene_id)
            gene_synonyms = self.common_names.get_synonyms(gene_id)

            attr = {
                "node_class": "State",
                "node_type": "Protein",
                "node_id": monomer_id,
                "name": monomer_name,
                "synonyms": monomer_synonyms,
                "url": URL_TEMPLATE.format(gene_id),
                "location": molecule_compartment(monomer_id),
            }

            protein_node.read_attributes(**attr)

            # Append protein node to node_list
            self.node_list.append(protein_node)

            # Initialize a single translation node for each transcript
            translation_node = Node()

            # Add attributes to the node
            translation_id = gene_id + NODE_ID_SUFFIX["translation"]
            translation_name = gene_name + " translation"
            if isinstance(gene_synonyms, list):
                translation_synonyms = [x + " translation" for x in gene_synonyms]
            else:
                translation_synonyms = [gene_synonyms]

            attr = {
                "node_class": "Process",
                "node_type": "Translation",
                "node_id": translation_id,
                "name": translation_name,
                "synonyms": translation_synonyms,
                "url": URL_TEMPLATE.format(gene_id),
                "location": molecule_compartment(translation_id),
            }
            translation_node.read_attributes(**attr)

            # Append translation node to node_list
            self.node_list.append(translation_node)

            # Add edges from all transcripts that encode for the protein to
            # the translation node
            rna_ids = [
                all_rna_ids[i]
                for i in self.sim_data.process.transcription.cistron_id_to_rna_indexes(
                    cistron_id
                )
            ]

            for rna_id in rna_ids:
                self._append_edge("Translation", rna_id, translation_id)

            # Add edge from translation to monomer node
            self._append_edge("Translation", translation_id, monomer_id)

            # Add edges from amino acids to translation node
            for aa_id in aa_ids:
                self._append_edge("Translation", aa_id, translation_id)

            # Add edges from other reactants to translation node
            for reactant_id in [gtp_id, water_id]:
                self._append_edge("Translation", reactant_id, translation_id)

            # Add edges from translation to other product nodes
            for product_id in [gdp_id, ppi_id, water_id]:
                self._append_edge("Translation", translation_id, product_id)

            # Add edges from ribosome subunits to translation node
            for subunit_id in ribosome_subunit_ids:
                self._append_edge("Translation", subunit_id, translation_id)

    def _add_complexation_and_complexes(self):
        """
        Add complexation process nodes and complex state nodes to the node
        list, and edges connected to the complexation nodes to the edge
        list.
        """
        # List of all complex IDs and reaction IDs
        complex_ids = (
            self.sim_data.process.complexation.ids_complexes
            + EQUILIBRIUM_COMPLEXES_IN_COMPLEXATION
        )
        reaction_ids = self.sim_data.process.complexation.ids_reactions

        molecule_ids = self.sim_data.process.complexation.molecule_names
        stoich_matrix = self.sim_data.process.complexation.stoich_matrix()

        # Loop through all complexation reactions
        for reaction_index, reaction_id in enumerate(reaction_ids):
            # Initialize a single complexation node for each complexation reaction
            complexation_node = Node()

            # Add attributes to the node
            attr = {
                "node_class": "Process",
                "node_type": "Complexation",
                "node_id": reaction_id,
                "name": reaction_id,
                "url": URL_TEMPLATE.format(reaction_id.replace("_RXN", "")),
                "location": molecule_compartment(reaction_id),
            }
            complexation_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(complexation_node)

            # Get reaction stoichiometry from stoichimetric matrix
            stoich_vector = stoich_matrix[:, reaction_index]
            molecule_indices = np.where(stoich_vector)[0]
            stoich_coeffs = stoich_vector[molecule_indices]

            # Loop through all proteins participating in the reaction
            for molecule_index, stoich in zip(molecule_indices, stoich_coeffs):
                # Add complexation edges
                # Note: the direction of the edge is determined by the sign of the
                # stoichiometric coefficient.
                if stoich > 0:
                    self._append_edge(
                        "Complexation",
                        reaction_id,
                        molecule_ids[molecule_index],
                        stoich,
                    )
                else:
                    self._append_edge(
                        "Complexation",
                        molecule_ids[molecule_index],
                        reaction_id,
                        stoich,
                    )

        for complex_id in complex_ids:
            # Initialize a single complex node for each complex
            complex_node = Node()

            # Add attributes to the node
            complex_id_no_compartment = complex_id[:-3]
            complex_name = self.common_names.get_common_name(complex_id_no_compartment)
            complex_synonyms = self.common_names.get_synonyms(complex_id_no_compartment)

            attr = {
                "node_class": "State",
                "node_type": "Complex",
                "node_id": complex_id,
                "name": complex_name,
                "synonyms": complex_synonyms,
                "url": URL_TEMPLATE.format(complex_id_no_compartment),
                "location": molecule_compartment(complex_id),
            }

            complex_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(complex_node)

    def _add_metabolism_and_metabolites(self):
        """
        Add metabolism process nodes and metabolite state nodes to the node
        list, add edges connected to the metabolism nodes to the edge list.
        Note: forward and reverse reactions are represented as separate nodes.
        """
        # Get all reaction stoichiometry from sim_data
        reaction_stoich = self.sim_data.process.metabolism.reaction_stoich

        # Get reaction to catalyst dict from sim_data
        reaction_catalysts = self.sim_data.process.metabolism.reaction_catalysts

        # get transport reactions and remove from metabolism
        transport_reactions = set(self.sim_data.process.metabolism.transport_reactions)

        # Initialize list of metabolite IDs
        metabolite_ids = []

        # Loop through all reactions
        for reaction_id, stoich_dict in reaction_stoich.items():
            node_type = "Metabolism"

            # make a transport node if the reaction is in transport_reactions
            if reaction_id in transport_reactions:
                node_type = "Transport"

            # Initialize a single metabolism node for each reaction
            metabolism_node = Node()

            # Get URL for metabolism reaction
            if reaction_id.startswith("TRANS"):
                reaction_url_tag = "-".join(reaction_id.split("-")[:3])
            elif reaction_id.startswith("RXN"):
                reaction_url_tag = "-".join(reaction_id.split("-")[:2])
            else:
                reaction_url_tag = reaction_id.split("-RXN")[0] + "-RXN"
            reaction_url = URL_TEMPLATE.format(
                reaction_url_tag.replace(" (reverse)", "")
            )

            # Add attributes to the node
            attr = {
                "node_class": "Process",
                "node_type": node_type,
                "node_id": reaction_id,
                "name": reaction_id,
                "url": reaction_url,
                "location": molecule_compartment(reaction_id),
            }
            metabolism_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(metabolism_node)

            # Get list of proteins that catalyze this reaction
            catalyst_list = reaction_catalysts.get(reaction_id, [])

            # Add an edge from each catalyst to the metabolism node
            for catalyst in catalyst_list:
                self._append_edge(node_type, catalyst, reaction_id)

            # Loop through all metabolites participating in the reaction
            for metabolite, stoich in stoich_dict.items():
                # Add metabolites that were not encountered
                if metabolite not in metabolite_ids:
                    metabolite_ids.append(metabolite)

                # Add Metabolism edges
                # Note: the direction of the edge is determined by the sign of the
                # stoichiometric coefficient.
                if stoich > 0:
                    self._append_edge(node_type, reaction_id, metabolite, stoich)
                else:
                    self._append_edge(node_type, metabolite, reaction_id, stoich)

        # Add specific charging reactions
        # TODO (Travis): add charged/uncharged tRNA as RNA not metabolites?
        transcription = self.sim_data.process.transcription
        uncharged_trnas = transcription.uncharged_trna_names
        charging_stoich = transcription.charging_stoich_matrix().T
        charging_molecules = np.array(transcription.charging_molecules)
        synthetases = np.array(transcription.synthetase_names)
        trna_to_synthetase = transcription.aa_from_trna.T.dot(
            transcription.aa_from_synthetase
        )

        for stoich, trna, synth_idx in zip(
            charging_stoich, uncharged_trnas, trna_to_synthetase
        ):
            rxn = "{} net charging".format(trna[:-3])

            charging_node = Node()
            attr = {
                "node_class": "Process",
                "node_type": "Charging",
                "node_id": rxn,
                "name": rxn,
                "location": molecule_compartment(trna),
            }
            charging_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(charging_node)

            for synthetase in synthetases[synth_idx != 0]:
                self._append_edge("Charging", synthetase, rxn, 1)

            # Loop through all metabolites participating in the reaction
            mol_idx = np.where(stoich != 0)[0]
            for mol, direction in zip(charging_molecules[mol_idx], stoich[mol_idx]):
                # Add metabolites that were not encountered
                if mol != trna and mol not in metabolite_ids:
                    metabolite_ids.append(mol)

                # Add Charging edges
                # Note: the direction of the edge is determined by the sign of the
                # stoichiometric coefficient.
                if direction > 0:
                    self._append_edge("Charging", rxn, mol, direction)
                else:
                    self._append_edge("Charging", mol, rxn, direction)

        # Loop through all metabolites
        for metabolite_id in metabolite_ids:
            # Skip proteins - they should have already been added
            if metabolite_id in PROTEINS_IN_METABOLISM:
                continue

            # Initialize a single metabolite node for each metabolite
            metabolite_node = Node()

            # Add attributes to the node
            metabolite_id_no_compartment = metabolite_id[:-3]
            metabolite_name = self.common_names.get_common_name(
                metabolite_id_no_compartment
            )
            metabolite_synonyms = self.common_names.get_synonyms(
                metabolite_id_no_compartment
            )

            attr = {
                "node_class": "State",
                "node_type": "Metabolite",
                "node_id": metabolite_id,
                "name": metabolite_name,
                "synonyms": metabolite_synonyms,
                "url": URL_TEMPLATE_COMPOUND.format(metabolite_id_no_compartment),
                "location": molecule_compartment(metabolite_id),
            }

            metabolite_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(metabolite_node)

    def _add_equilibrium(self):
        """
        Add equilibrium nodes to the node list, and add edges connected to the
        equilibrium nodes to the edge list.
        """
        # Get equilibrium-specific data from sim_data
        equilibrium_molecule_ids = self.sim_data.process.equilibrium.molecule_names
        equilibrium_reaction_ids = self.sim_data.process.equilibrium.rxn_ids
        equilibrium_stoich_matrix = self.sim_data.process.equilibrium.stoich_matrix()

        # Get IDs of complexes that were already added
        complexation_complex_ids = self.sim_data.process.complexation.ids_complexes

        # Get list of complex IDs in equilibrium
        equilibrium_complex_ids = self.sim_data.process.equilibrium.ids_complexes

        # Loop through each equilibrium reaction
        for reaction_index, reaction_id in enumerate(equilibrium_reaction_ids):
            # Initialize a single equilibrium node for each equilibrium reaction
            equilibrium_node = Node()

            # Add attributes to the node
            reaction_name = reaction_id[:-4] + " equilibrium rxn"
            attr = {
                "node_class": "Process",
                "node_type": "Equilibrium",
                "node_id": reaction_id,
                "name": reaction_name,
                "location": molecule_compartment(reaction_id),
            }
            equilibrium_node.read_attributes(**attr)

            # Append new node to node_list
            self.node_list.append(equilibrium_node)

            # Extract column corresponding to reaction in the stoichiometric matrix
            equilibrium_stoich_matrix_column = equilibrium_stoich_matrix[
                :, reaction_index
            ]

            # Loop through each element in column
            for molecule_index, stoich in enumerate(equilibrium_stoich_matrix_column):
                if stoich == 0:
                    continue

                molecule_id = equilibrium_molecule_ids[molecule_index]

                # Add Equilibrium edges
                # Note: the direction of the edge is determined by the sign of the
                # stoichiometric coefficient.
                if stoich > 0:
                    self._append_edge("Equilibrium", reaction_id, molecule_id, stoich)
                else:
                    self._append_edge("Equilibrium", molecule_id, reaction_id, stoich)

        # Get 2CS-specific data from sim_data
        tcs_molecule_ids = self.sim_data.process.two_component_system.molecule_names
        tcs_reaction_ids = self.sim_data.process.two_component_system.rxn_ids
        tcs_stoich_matrix = self.sim_data.process.two_component_system.stoich_matrix()

        # Initialize list of complex IDs in 2CS
        # TODO (ggsun): add this to sim_data
        tcs_complex_ids = []

        # Get lists of monomers that were already added
        monomer_ids = list(self.sim_data.process.translation.monomer_data["id"])

        # Loop through each 2CS reaction
        for reaction_index, reaction_id in enumerate(tcs_reaction_ids):
            # Initialize a single equilibrium node for each equilibrium reaction
            equilibrium_node = Node()

            # Add attributes to the node
            reaction_name = reaction_id[:-4] + " 2CS rxn"
            attr = {
                "node_class": "Process",
                "node_type": "Equilibrium",
                "node_id": reaction_id,
                "name": reaction_name,
                "location": molecule_compartment(reaction_id),
            }
            equilibrium_node.read_attributes(**attr)

            # Append new node to node_list
            self.node_list.append(equilibrium_node)

            # Extract column corresponding to reaction in the stoichiometric matrix
            tcs_stoich_matrix_column = tcs_stoich_matrix[:, reaction_index]

            # Loop through each element in column
            for molecule_index, stoich in enumerate(tcs_stoich_matrix_column):
                if stoich == 0:
                    continue

                molecule_id = tcs_molecule_ids[molecule_index]

                if molecule_id not in monomer_ids + NONPROTEIN_MOLECULES_IN_2CS:
                    tcs_complex_ids.append(molecule_id)

                # Add Equilibrium edges
                # Note: the direction of the edge is determined by the sign of the
                # stoichiometric coefficient.
                if stoich > 0:
                    self._append_edge("Equilibrium", reaction_id, molecule_id, stoich)
                else:
                    self._append_edge("Equilibrium", molecule_id, reaction_id, stoich)

        # Add new complexes that were encountered here
        for complex_id in list(set(equilibrium_complex_ids + tcs_complex_ids)):
            if complex_id in complexation_complex_ids:
                continue

            # Initialize a single complex node for each complex
            complex_node = Node()

            # Add attributes to the node
            complex_id_no_compartment = complex_id[:-3]
            complex_name = self.common_names.get_common_name(complex_id_no_compartment)

            attr = {
                "node_class": "State",
                "node_type": "Complex",
                "node_id": complex_id,
                "name": complex_name,
                "location": molecule_compartment(complex_id),
            }
            complex_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(complex_node)

        # Loop through metabolites that only appear in equilibrium
        for metabolite_id in METABOLITES_ONLY_IN_EQUILIBRIUM:
            # Initialize a single metabolite node for each metabolite
            metabolite_node = Node()

            # Add attributes to the node
            metabolite_id_no_compartment = metabolite_id[:-3]
            metabolite_name = self.common_names.get_common_name(
                metabolite_id_no_compartment
            )

            attr = {
                "node_class": "State",
                "node_type": "Metabolite",
                "node_id": metabolite_id,
                "name": metabolite_name,
                "location": molecule_compartment(metabolite_id),
            }
            metabolite_node.read_attributes(**attr)

            # Append node to node_list
            self.node_list.append(metabolite_node)

    def _add_regulation(self):
        """
        Add regulation nodes with to the node list, and add edges connected to
        the regulation nodes to the edge list.
        """
        # Get list of transcription factor IDs and transcription unit IDs
        tf_ids = self.sim_data.process.transcription_regulation.tf_ids
        rna_ids = self.sim_data.process.transcription.rna_data["id"]
        cistron_ids = self.sim_data.process.transcription.cistron_data["id"]

        # Get delta_prob matrix from sim_data
        delta_prob = self.sim_data.process.transcription_regulation.delta_prob

        # Build dict that maps TFs to indexes of transcription units they
        # regulate
        TF_to_TU_idx = {}

        for i, tf in enumerate(tf_ids):
            TF_to_TU_idx[tf] = delta_prob["deltaI"][delta_prob["deltaJ"] == i]

        # Build dict that maps RNA IDs to gene IDs
        cistron_id_to_gene_id = {}

        for cistron_id, gene_id in zip(
            self.sim_data.process.replication.gene_data["cistron_id"],
            self.sim_data.process.replication.gene_data["name"],
        ):
            cistron_id_to_gene_id[cistron_id] = gene_id

        # Loop through all TFs
        for tf_id in tf_ids:
            # Add TF bound to DNA
            bound_tf_id = f"{tf_id}-bound"
            tf_node = Node()
            attr = {
                "node_class": "Process",
                "node_type": "TF Binding",
                "node_id": bound_tf_id,
                "name": bound_tf_id,
                "location": "c",
            }
            tf_node.read_attributes(**attr)
            self.node_list.append(tf_node)
            self._append_edge("Regulation", tf_id + "[c]", bound_tf_id)

            # Get IDs of RNAs that are regulated by the TF
            regulated_rna_ids = rna_ids[TF_to_TU_idx[tf_id]]

            for regulated_rna_id in regulated_rna_ids:
                regulated_cistron_ids = [
                    cistron_ids[i]
                    for i in self.sim_data.process.transcription.rna_id_to_cistron_indexes(
                        regulated_rna_id
                    )
                ]

                for regulated_cistron_id in regulated_cistron_ids:
                    # Find corresponding ID of gene
                    gene_id = cistron_id_to_gene_id[regulated_cistron_id]

                    # Initialize a single regulation node for each TF-gene pair
                    regulation_node = Node()

                    # Add attributes to the node
                    reg_id = tf_id + "_" + gene_id + NODE_ID_SUFFIX["regulation"]
                    reg_name = tf_id + "-" + gene_id + " gene regulation"
                    attr = {
                        "node_class": "Process",
                        "node_type": "Regulation",
                        "node_id": reg_id,
                        "name": reg_name,
                        "location": molecule_compartment(reg_id),
                    }
                    regulation_node.read_attributes(**attr)

                    self.node_list.append(regulation_node)

                    # Add edge from TF to this regulation node
                    self._append_edge("Regulation", bound_tf_id, reg_id)

                    # Add edge from this regulation node to the gene
                    self._append_edge("Regulation", reg_id, gene_id)

    def _find_duplicate_nodes(self):
        """
        Identify nodes that have duplicate IDs.
        """
        counters = Counter([node.get_node_id() for node in self.node_list])
        duplicate_ids = {node_id for node_id in counters if counters[node_id] > 1}

        # Print duplicate node IDs that were found
        if len(duplicate_ids) > 0:
            raise ValueError(
                "%d node IDs have duplicates: %s" % (len(duplicate_ids), duplicate_ids)
            )

    def _remove_hanging_edges(self):
        """
        Remove edges that are not connected to existing nodes.
        """
        # Get set of all node IDs
        node_ids = {node.node_id for node in self.node_list}
        disconnected_edge_indexes = []

        # Find edges whose source and destination nodes are not defined
        for index, edge in enumerate(self.edge_list):
            if edge.src_id not in node_ids or edge.dst_id not in node_ids:
                disconnected_edge_indexes.append(index)

        # Remove these edges
        for index in disconnected_edge_indexes[::-1]:
            self.edge_list.pop(index)

    def _append_edge(
        self, type_: str, src: str, dst: str, stoichiometry: Optional[str | int] = ""
    ):
        """
        Helper function for appending new nodes to the network.
        """
        edge = Edge(type_)
        edge.read_attributes(src_id=src, dst_id=dst, stoichiometry=stoichiometry)
        self.edge_list.append(edge)
