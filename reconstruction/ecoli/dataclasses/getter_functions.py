"""
SimulationData getter functions
"""

import itertools
import re

from Bio.Seq import Seq
import numpy as np
import numpy.typing as npt

from reconstruction.ecoli.dataclasses.molecule_groups import POLYMERIZED_FRAGMENT_PREFIX
from wholecell.utils import units

EXCLUDED_RNA_TYPES = {"pseudo", "phantom"}

# Mapping of compartment IDs to abbreviations for compartments undefined in
# flat/compartments.tsv
UNDEFINED_COMPARTMENT_IDS_TO_ABBREVS = {
    "CCI-PERI-BAC-GN": "p",
    "CCI-PM-BAC-NEG-GN": "i",
    "CCO-PM-BAC-ACT": "i",
    "CCO-OUT": "e",
    "CCO-IN": "c",
    "NIL": "c",
    "CCO-CW-BAC-NEG": "o",
    "CCO-CE-BAC": "m",
    "CCO-BAC-NUCLEOID": "c",
    "CCO-RIBOSOME": "c",
}

IGNORED_DNA_SITE_TYPES = {
    "dna-binding-site",
    "phage-attachment-site",
    "rep-element",
    "repeat-region",
}


class TranscriptionDirectionError(Exception):
    pass


class UnknownMolecularWeightError(Exception):
    pass


class UnknownModifiedRnaError(Exception):
    pass


class InvalidProteinComplexError(Exception):
    pass


class GetterFunctions(object):
    """getterFunctions"""

    _compartment_tag = re.compile(r"\[[a-z]]")

    def __init__(self, raw_data, sim_data):
        self._n_submass_indexes = len(sim_data.submass_name_to_index)
        self._submass_name_to_index = sim_data.submass_name_to_index

        self._build_sequences(raw_data)
        self._build_all_masses(raw_data, sim_data)
        self._build_compartments(raw_data, sim_data)
        self._build_genomic_coordinates(raw_data)

    def get_sequences(self, ids: list[str] | np.ndarray) -> list[str]:
        """
        Return a list of sequences of the molecules with the given IDs.
        """
        assert isinstance(ids, (list, np.ndarray))
        return [self._sequences[mol_id] for mol_id in ids]

    def get_mass(self, mol_id: str):
        """
        Return the total mass of the molecule with a given ID.
        """
        assert isinstance(mol_id, str)
        return (
            self._mass_units
            * self._all_total_masses[self._compartment_tag.sub("", mol_id)]
        )

    def get_masses(self, mol_ids: list | np.ndarray):
        """
        Return an array of total masses of the molecules with the given IDs.
        """
        assert isinstance(mol_ids, (list, np.ndarray))
        masses = [
            self._all_total_masses[self._compartment_tag.sub("", mol_id)]
            for mol_id in mol_ids
        ]
        return self._mass_units * np.array(masses)

    def get_submass_array(self, mol_id: str):
        """
        Return the submass array of the molecule with a given ID.
        """
        assert isinstance(mol_id, str)
        return (
            self._mass_units
            * self._all_submass_arrays[self._compartment_tag.sub("", mol_id)]
        )

    def get_compartment(self, mol_id: str) -> list[list[str]]:
        """
        Returns the list of one-letter codes for the compartments that the
        molecule with the given ID can exist in.
        """
        assert isinstance(mol_id, str)
        return self._all_compartments[mol_id]

    def get_compartments(self, ids: list[str] | np.ndarray) -> list[list[str]]:
        """
        Returns a list of the list of one-letter codes for the compartments
        that each of the molecules with the given IDs can exist in.
        """
        assert isinstance(ids, (list, np.ndarray))
        return [self._all_compartments[x] for x in ids]

    def get_compartment_tag(self, id_: str) -> str:
        """
        Look up a molecule id and return a compartment suffix tag like '[c]'.
        """
        return f"[{self._all_compartments[id_][0]}]"

    def is_valid_molecule(self, mol_id: str) -> bool:
        """
        Returns True if the molecule with the given ID is a valid molecule (has
        both a submass array and a compartment tag).
        """
        return mol_id in self._all_submass_arrays and mol_id in self._all_compartments

    def get_all_valid_molecules(self) -> list[str]:
        """
        Returns a list of all molecule IDs with assigned submass arrays and
        compartments.
        """
        return sorted(self._all_submass_arrays.keys() & self._all_compartments.keys())

    def get_genomic_coordinates(self, site_id: str) -> tuple[int, int]:
        """
        Returns the genomic coordinates of the left and right ends of a DNA site
        given the ID of the site.
        """
        assert isinstance(site_id, str)
        return self._all_genomic_coordinates[site_id]

    def get_miscrnas_with_singleton_tus(self) -> list[str]:
        """
        Returns a list of all miscRNA IDs with corresponding single-gene
        transcription units.
        """
        return sorted(self._miscrna_id_to_singleton_tu_id.keys())

    def get_singleton_tu_id(self, rna_id: str) -> str:
        """
        Returns the ID of the single-gene transcription unit corresponding to
        the given miscRNA ID, if such a transcription unit exists. This is
        necessary to replace some references to miscRNA IDs in complexation or
        equilibrium reactions with their corresponding TU IDs, which are the
        molecules that are actually transcribed.
        """
        assert isinstance(rna_id, str)
        return self._miscrna_id_to_singleton_tu_id[rna_id]

    def _build_sequences(self, raw_data):
        """
        Builds sequences of RNAs and proteins.
        """
        self._sequences = {}
        self._build_rna_sequences(raw_data)
        self._build_protein_sequences(raw_data)

    def _build_rna_sequences(self, raw_data):
        """
        Builds nucleotide sequences of each transcription unit using the genome
        sequence and the left and right end positions.
        """
        genome_sequence = raw_data.genome_sequence

        def parse_sequence(tu_id, left_end_pos, right_end_pos, direction):
            """
            Parses genome sequence to get transcription unit sequence, given
            left and right end positions and transcription direction (Note:
            the left and right end positions in the raw data files are given as
            1-indexed coordinates)
            """
            if direction == "+":
                return genome_sequence[left_end_pos - 1 : right_end_pos].transcribe()
            elif direction == "-":
                return (
                    genome_sequence[left_end_pos - 1 : right_end_pos]
                    .reverse_complement()
                    .transcribe()
                )
            else:
                raise TranscriptionDirectionError(
                    f"Unidentified transcription direction given for {tu_id}"
                )

        # Get set of valid gene IDs that have positions on the chromosome, and
        # is not a pseudogene or a phantom gene
        gene_id_to_rna_type = {rna["gene_id"]: rna["type"] for rna in raw_data.rnas}
        valid_gene_ids = {
            gene["id"]
            for gene in raw_data.genes
            if gene["left_end_pos"] is not None
            and gene["right_end_pos"] is not None
            and gene_id_to_rna_type[gene["id"]] not in EXCLUDED_RNA_TYPES
        }

        # Set of gene IDs that are covered by listed transcription units
        covered_gene_ids = set()

        # Keep track of gene tuples of TUs to remove duplicate TUs that cover
        # the same set of genes but have different left & right end coordinates.
        # The first TU in the list that covers the given set of genes is always
        # selected over later TUs. Later TUs are discarded, but their evidence
        # codes are added to the list of evidence codes of the first TU.
        gene_tuple_to_tu_index = {}

        # Add sequences from transcription_units file
        gene_id_to_left_end_pos = {
            gene["id"]: gene["left_end_pos"] for gene in raw_data.genes
        }
        gene_id_to_right_end_pos = {
            gene["id"]: gene["right_end_pos"] for gene in raw_data.genes
        }
        gene_id_to_rna_id = {gene["id"]: gene["rna_ids"][0] for gene in raw_data.genes}
        gene_id_to_direction = {
            gene["id"]: gene["direction"] for gene in raw_data.genes
        }
        self._miscrna_id_to_singleton_tu_id = {}

        for i, tu in enumerate(raw_data.transcription_units):
            # Get list of genes in TU after excluding invalid genes
            gene_tuple = tuple(
                sorted(
                    [gene_id for gene_id in tu["genes"] if gene_id in valid_gene_ids]
                )
            )

            # Skip TUs with only invalid genes
            if len(gene_tuple) == 0:
                continue

            # Skip duplicate TUs but compile all evidence codes
            if gene_tuple in gene_tuple_to_tu_index:
                evidence_list = raw_data.transcription_units[
                    gene_tuple_to_tu_index[gene_tuple]
                ]["evidence"]
                new_evidence = tu["evidence"]

                # Add nonduplicate evidence codes to original list
                evidence_list.extend(list(set(new_evidence) - set(evidence_list)))
                continue
            else:
                gene_tuple_to_tu_index[gene_tuple] = i

            # Use gene coordinates if left and right end positions are not given
            if not isinstance(tu["left_end_pos"], int):
                tu["left_end_pos"] = min(
                    [gene_id_to_left_end_pos[gene_id] for gene_id in tu["genes"]]
                )
            if not isinstance(tu["right_end_pos"], int):
                tu["right_end_pos"] = max(
                    [gene_id_to_right_end_pos[gene_id] for gene_id in tu["genes"]]
                )

            # Keep track of genes that are covered
            covered_gene_ids |= set(tu["genes"])

            # Add mapping from miscRNA IDs to single-gene TU IDs
            if len(gene_tuple) == 1 and gene_id_to_rna_type[gene_tuple[0]] == "miscRNA":
                self._miscrna_id_to_singleton_tu_id[
                    gene_id_to_rna_id[gene_tuple[0]]
                ] = tu["id"]

            self._sequences[tu["id"]] = parse_sequence(
                tu["id"], tu["left_end_pos"], tu["right_end_pos"], tu["direction"]
            )

        # Add sequences of individual RNAs that are not part of any
        # transcription unit (these genes are assumed to be transcribed as
        # monocistronic transcription units)
        rna_id_to_gene_id = {gene["rna_ids"][0]: gene["id"] for gene in raw_data.genes}
        all_rna_ids = sorted(set([rna["id"] for rna in raw_data.rnas]))

        for rna_id in all_rna_ids:
            # Skip RNAs without associated genes
            if rna_id not in rna_id_to_gene_id:
                continue

            gene_id = rna_id_to_gene_id[rna_id]

            # Skip excluded genes
            if gene_id not in valid_gene_ids:
                continue

            # Skip mRNAs that are already covered by transcription units
            if gene_id in covered_gene_ids and gene_id_to_rna_type[gene_id] == "mRNA":
                continue

            left_end_pos = gene_id_to_left_end_pos[gene_id]
            right_end_pos = gene_id_to_right_end_pos[gene_id]

            self._sequences[rna_id] = parse_sequence(
                rna_id, left_end_pos, right_end_pos, gene_id_to_direction[gene_id]
            )

    def _build_protein_sequences(self, raw_data):
        """
        Builds the amino acid sequences of each protein monomer using sequences
        in raw_data.
        """
        for protein in raw_data.proteins:
            if protein["seq"] is not None:
                # Remove internal stop codons
                self._sequences[protein["id"]] = Seq(protein["seq"].replace("*", ""))

    def _build_submass_array(
        self, mw: float, submass_name: str
    ) -> npt.NDArray[np.float64]:
        """
        Converts a scalar molecular weight value to an array of submasses,
        given the name of the submass category that the molecule belongs to.
        """
        mw_array = np.zeros(self._n_submass_indexes)
        mw_array[self._submass_name_to_index[submass_name]] = mw
        return mw_array

    def _build_all_masses(self, raw_data, sim_data):
        """
        Builds dictionary of molecular weights keyed with the IDs of molecules.
        Depending on the type of the molecule, weights are either pulled
        directly from raw data, or dynamically calculated from the existing
        data.
        """
        self._all_submass_arrays = {}

        metabolites_with_masses = [
            met for met in raw_data.metabolites if met["mw"] is not None
        ]

        # Water is the only metabolite with a separate submass classification
        self._all_submass_arrays.update(
            {
                met["id"]: (
                    self._build_submass_array(met["mw"], "metabolite")
                    if met["id"] != sim_data.molecule_ids.water[:-3]
                    else self._build_submass_array(met["mw"], "water")
                )
                for met in metabolites_with_masses
            }
        )

        # These updates can be dependent on metabolite masses
        self._all_submass_arrays.update(
            self._build_polymerized_subunit_masses(sim_data)
        )
        self._all_submass_arrays.update(self._build_rna_masses(raw_data, sim_data))
        self._all_submass_arrays.update(self._build_protein_masses(raw_data, sim_data))
        self._all_submass_arrays.update(
            self._build_full_chromosome_mass(raw_data, sim_data)
        )

        # These updates can be dependent on the masses calculated above
        self._all_submass_arrays.update(self._build_modified_rna_masses(raw_data))
        self._all_submass_arrays.update(self._build_protein_complex_masses(raw_data))

        # This update can be dependent on the masses calculated above
        self._all_submass_arrays.update(
            self._build_modified_protein_masses(raw_data, sim_data)
        )

        self._mass_units = units.g / units.mol

        # Build dictionary of total masses of each molecule
        self._all_total_masses = {
            mol_id: mass_array.sum()
            for (mol_id, mass_array) in self._all_submass_arrays.items()
        }

    def _build_polymerized_subunit_masses(self, sim_data):
        """
        Builds dictionary of molecular weights of polymerized subunits (NTPs,
        dNTPs, and amino acids) by subtracting the end weights from the weights
        of original subunits.
        """
        polymerized_subunit_masses = {}

        def add_subunit_masses(subunit_ids, end_group_id):
            """
            Add subunit masses to dictionary given the IDs of the original
            subunits and the end group.
            """
            # Subtract mw of end group from each polymer subunit
            end_group_mw = self._all_submass_arrays[end_group_id[:-3]].sum()
            polymerized_subunit_mws = [
                self._all_submass_arrays[met_id[:-3]].sum() - end_group_mw
                for met_id in subunit_ids
            ]

            # Add to dictionary with prefixed ID
            polymerized_subunit_masses.update(
                {
                    POLYMERIZED_FRAGMENT_PREFIX
                    + subunit_id[:-3]: self._build_submass_array(mw, "metabolite")
                    for subunit_id, mw in zip(subunit_ids, polymerized_subunit_mws)
                }
            )

        add_subunit_masses(sim_data.molecule_groups.ntps, sim_data.molecule_ids.ppi)
        add_subunit_masses(sim_data.molecule_groups.dntps, sim_data.molecule_ids.ppi)
        add_subunit_masses(
            sim_data.molecule_groups.amino_acids, sim_data.molecule_ids.water
        )

        return polymerized_subunit_masses

    def _build_rna_masses(self, raw_data, sim_data):
        """
        Builds dictionary of molecular weights of RNAs keyed with the RNA IDs.
        Molecular weights are calculated from the RNA sequence and the weights
        of polymerized NTPs.
        """
        rnas_with_seqs = [
            rna["id"]
            for rna in itertools.chain(raw_data.rnas, raw_data.transcription_units)
            if rna["id"] in self._sequences
        ]

        # Get RNA nucleotide compositions
        rna_seqs = self.get_sequences(rnas_with_seqs)
        nt_counts = []
        for seq in rna_seqs:
            nt_counts.append(
                [seq.count(letter) for letter in sim_data.ntp_code_to_id_ordered.keys()]
            )
        nt_counts = np.array(nt_counts)

        # Calculate molecular weights
        ppi_mw = self._all_submass_arrays[sim_data.molecule_ids.ppi[:-3]].sum()
        polymerized_ntp_mws = np.array(
            [
                self._all_submass_arrays[met_id[:-3]].sum()
                for met_id in sim_data.molecule_groups.polymerized_ntps
            ]
        )

        mws = nt_counts.dot(polymerized_ntp_mws) + ppi_mw  # Add end weight

        # Map monocistronic RNA IDs to RNA types
        rna_id_to_type = {rna["id"]: rna["type"] for rna in raw_data.rnas}

        # Add polycistronic RNAs to mapping
        gene_id_to_rna_id = {gene["id"]: gene["rna_ids"][0] for gene in raw_data.genes}

        for tu in raw_data.transcription_units:
            if tu["id"] not in self._sequences:
                continue
            tu_rna_types = [
                rna_id_to_type[gene_id_to_rna_id[gene]]
                for gene in tu["genes"]
                if rna_id_to_type[gene_id_to_rna_id[gene]] not in EXCLUDED_RNA_TYPES
            ]
            if len(set(tu_rna_types)) > 1 and set(tu_rna_types) != {"rRNA", "tRNA"}:
                raise ValueError(
                    f"Transcription unit {tu['id']} includes genes"
                    f" that encode for two or more different types of RNAs."
                    f" Such transcription units are not supported by this"
                    f" version of the model with the exception of rRNA"
                    f" transcription units with tRNA genes."
                )

            if len(tu_rna_types) == 1:
                rna_id_to_type[tu["id"]] = tu_rna_types[0]
            else:
                # Hybrid RNAs are set to have nonspecific mass
                rna_id_to_type[tu["id"]] = "nonspecific_RNA"

        return {
            rna_id: self._build_submass_array(mw, rna_id_to_type[rna_id])
            for (rna_id, mw) in zip(rnas_with_seqs, mws)
        }

    def _build_protein_masses(self, raw_data, sim_data):
        """
        Builds dictionary of molecular weights of protein monomers keyed with
        the protein IDs. Molecular weights are calculated from the protein
        sequence and the weights of polymerized amino acids.
        """
        proteins_with_seqs = [
            protein["id"]
            for protein in raw_data.proteins
            if protein["id"] in self._sequences
        ]

        # Get protein amino acid compositions
        protein_seqs = self.get_sequences(proteins_with_seqs)
        aa_counts = []
        for seq in protein_seqs:
            aa_counts.append(
                [
                    seq.count(letter)
                    for letter in sim_data.amino_acid_code_to_id_ordered.keys()
                ]
            )
        aa_counts = np.array(aa_counts)

        # Calculate molecular weights
        water_mw = self._all_submass_arrays[sim_data.molecule_ids.water[:-3]].sum()
        polymerized_aa_mws = np.array(
            [
                self._all_submass_arrays[met_id[:-3]].sum()
                for met_id in sim_data.molecule_groups.polymerized_amino_acids
            ]
        )
        mws = aa_counts.dot(polymerized_aa_mws) + water_mw  # Add end weight

        return {
            protein_id: self._build_submass_array(mw, "protein")
            for (protein_id, mw) in zip(proteins_with_seqs, mws)
        }

    def _build_full_chromosome_mass(self, raw_data, sim_data):
        """
        Calculates the mass of the full chromosome from its sequence and the
        weigths of polymerized dNTPs.
        """
        # Get chromosome dNTP compositions
        chromosome_seq = raw_data.genome_sequence
        forward_strand_nt_counts = np.array(
            [
                chromosome_seq.count(letter)
                for letter in sim_data.dntp_code_to_id_ordered.keys()
            ]
        )
        reverse_strand_nt_counts = np.array(
            [
                chromosome_seq.reverse_complement().count(letter)
                for letter in sim_data.dntp_code_to_id_ordered.keys()
            ]
        )

        # Calculate molecular weight
        polymerized_dntp_mws = np.array(
            [
                self._all_submass_arrays[met_id[:-3]].sum()
                for met_id in sim_data.molecule_groups.polymerized_dntps
            ]
        )
        mw = float(
            np.dot(
                forward_strand_nt_counts + reverse_strand_nt_counts,
                polymerized_dntp_mws,
            )
        )

        return {
            sim_data.molecule_ids.full_chromosome[:-3]: self._build_submass_array(
                mw, "DNA"
            )
        }

    def _build_modified_rna_masses(self, raw_data):
        """
        Builds dictionary of molecular weights of modified RNAs keyed with
        the molecule IDs. Molecular weights are calculated from the
        stoichiometries of the modification reactions.
        """
        modified_rna_masses = {}

        # Get all modified form IDs from RNA data
        all_modified_rna_ids = {
            modified_rna_id
            for rna in raw_data.rnas
            for modified_rna_id in rna["modified_forms"]
        }

        # Loop through each charging reaction
        for rxn in raw_data.trna_charging_reactions:
            # Find molecule IDs whose masses are still unknown
            unknown_mol_ids = [
                mol_id
                for mol_id in rxn["stoichiometry"].keys()
                if mol_id not in self._all_submass_arrays
            ]
            # The only molecule whose mass is unknown should be the modified
            # RNA molecule
            if len(unknown_mol_ids) != 1:
                raise UnknownMolecularWeightError(
                    "RNA modification reaction %s has two or more reactants or products with unknown molecular weights: %s"
                    % (rxn["id"], unknown_mol_ids)
                )

            # All modified RNAs must be assigned to a specific RNA
            modified_rna_id = unknown_mol_ids[0]
            if modified_rna_id not in all_modified_rna_ids:
                raise UnknownModifiedRnaError(
                    "The modified RNA molecule %s was not assigned to any RNAs."
                    % (modified_rna_id,)
                )

            # Calculate mw of the modified RNA from the stoichiometry
            mw_sum = np.zeros(self._n_submass_indexes)
            for mol_id, coeff in rxn["stoichiometry"].items():
                if mol_id == modified_rna_id:
                    modified_rna_coeff = coeff
                else:
                    mw_sum += coeff * self._all_submass_arrays[mol_id]

            modified_rna_masses[modified_rna_id] = -mw_sum / modified_rna_coeff

        return modified_rna_masses

    def _build_protein_complex_masses(self, raw_data):
        """
        Builds dictionary of molecular weights of protein complexes keyed with
        the molecule IDs. Molecular weights are calculated from the
        stoichiometries of the complexation/equilibrium reactions. For
        complexes whose subunits are also complexes, the molecular weights are
        calculated recursively.
        """
        protein_complex_masses = {}

        # Build mapping from complex ID to its subunit stoichiometry
        complex_id_to_stoich = {}

        for rxn in itertools.chain(
            raw_data.complexation_reactions, raw_data.equilibrium_reactions
        ):
            # Get the ID of the complex and the stoichiometry of subunits
            complex_ids = []
            subunit_stoich = {}

            for mol_id, coeff in rxn["stoichiometry"].items():
                # Replace miscRNA subunit IDs with TU IDs
                if mol_id in self._miscrna_id_to_singleton_tu_id:
                    mol_id = self.get_singleton_tu_id(mol_id)

                # Assume coefficients given as "null" equate to -1
                if coeff is None:
                    coeff = -1

                if coeff == 1:
                    complex_ids.append(mol_id)
                elif coeff < 0:
                    subunit_stoich.update({mol_id: -coeff})

                # Each molecule should either be a complex with the coefficient
                # 1 or a subunit with a negative coefficient
                else:
                    raise InvalidProteinComplexError(
                        "Reaction %s contains an invalid molecule %s with stoichiometric coefficient %.1f."
                        % (rxn["id"], mol_id, coeff)
                    )

            # Each reaction should only produce a single protein complex
            if len(complex_ids) != 1:
                raise InvalidProteinComplexError(
                    "Reaction %s does not produce a single protein complex."
                    % (rxn["id"],)
                )

            complex_id_to_stoich[complex_ids[0]] = subunit_stoich

        def get_mw(mol_id):
            """
            Recursive function that returns the molecular weight given a
            molecule ID.
            """
            # Look for molecule ID in existing dictionaries, and return
            # molecular weight array if found
            if mol_id in self._all_submass_arrays:
                return self._all_submass_arrays[mol_id]
            elif mol_id in protein_complex_masses:
                return protein_complex_masses[mol_id]

            # If molecule ID does not exist, recursively add up the molecular
            # weights of the molecule's subunits
            else:
                # Each complex molecule should have a corresponding subunit
                # stoichiometry
                if mol_id not in complex_id_to_stoich:
                    raise InvalidProteinComplexError(
                        "Complex %s is not being produced by any complexation or equilibrium reaction."
                        % (mol_id,)
                    )
                mw_sum = np.zeros(self._n_submass_indexes)

                for subunit_id, coeff in complex_id_to_stoich[mol_id].items():
                    mw_sum += coeff * get_mw(subunit_id)

                return mw_sum

        # Get molecular weights of each protein complex
        for complex_id in complex_id_to_stoich.keys():
            protein_complex_masses.update({complex_id: get_mw(complex_id)})

        return protein_complex_masses

    def _build_modified_protein_masses(self, raw_data, sim_data):
        """
        Builds dictionary of molecular weights of modified proteins keyed with
        the molecule IDs. Molecular weights are calculated from the
        stoichiometries of the modification reactions.
        """
        modified_protein_masses = {}

        # List IDs of all metabolites involved in 2CS
        ALL_2CS_METABOLITES = ["ATP", "ADP", "Pi", "WATER", "PROTON"]

        # Map types of modified proteins to their phosphorylation reactions that
        # can be used to calculate their molecular weights
        PROTEIN_TYPE_TO_PHOSPHORYLATION_RXN_ID = {
            "PHOSPHO-HK": "POS-HK-PHOSPHORYLATION_RXN",
            "PHOSPHO-HK-LIGAND": "POS-LIGAND-BOUND-HK-PHOSPHORYLATION_RXN",
            "PHOSPHO-RR": "POS-RR-DEPHOSPHORYLATION_RXN",
        }

        # Get stoichiometries of all 2CS reaction types
        all_2cs_reaction_stoichs = {
            rxn["id"]: {mol["molecule"]: mol["coeff"] for mol in rxn["stoichiometry"]}
            for rxn in raw_data.two_component_system_templates
        }

        all_2cs_systems = [sys["molecules"] for sys in raw_data.two_component_systems]

        for system in all_2cs_systems:
            for mol_type, mol_id in system.items():
                # Skip molecules that are not modified proteins
                if mol_type not in PROTEIN_TYPE_TO_PHOSPHORYLATION_RXN_ID:
                    continue
                else:
                    # Get reaction stoichiometry of phosphorylation reaction
                    phosphorylation_reaction = PROTEIN_TYPE_TO_PHOSPHORYLATION_RXN_ID[
                        mol_type
                    ]
                    reaction_stoich = all_2cs_reaction_stoichs[phosphorylation_reaction]

                    # Initialize submass array
                    mw = np.zeros(self._n_submass_indexes)
                    coeff_modified_protein = None

                    # Calculate mass such that reaction is mass balanced
                    for mol, coeff in reaction_stoich.items():
                        if mol == mol_type:
                            coeff_modified_protein = coeff
                            continue
                        elif mol in ALL_2CS_METABOLITES:
                            mw += -coeff * self._all_submass_arrays[mol]
                        else:
                            mw += -coeff * self._all_submass_arrays[system[mol]]

                    mw = mw / coeff_modified_protein

                    # Remove water submass and add to metabolite submass
                    if mw[sim_data.submass_name_to_index["water"]] != 0:
                        mw[sim_data.submass_name_to_index["metabolite"]] += mw[
                            sim_data.submass_name_to_index["water"]
                        ]
                        mw[sim_data.submass_name_to_index["water"]] = 0

                    modified_protein_masses[mol_id] = mw

        return modified_protein_masses

    def _build_compartments(self, raw_data, sim_data):
        self._all_compartments = {}
        all_compartments = [comp["abbrev"] for comp in raw_data.compartments]

        compartment_ids_to_abbreviations = {
            comp["id"]: comp["abbrev"] for comp in raw_data.compartments
        }
        # TODO (ggsun): Add some of these to list of compartments?
        compartment_ids_to_abbreviations.update(UNDEFINED_COMPARTMENT_IDS_TO_ABBREVS)

        # RNAs, modified RNAs, and full chromosomes only localize to the
        # cytosol
        self._all_compartments.update(
            {
                rna["id"]: ["c"]
                for rna in itertools.chain(raw_data.rnas, raw_data.transcription_units)
                if rna["id"] in self._sequences
            }
        )
        self._all_compartments.update(
            {
                modified_rna_id: ["c"]
                for rna in raw_data.rnas
                for modified_rna_id in rna["modified_forms"]
                if modified_rna_id in self._all_submass_arrays
            }
        )
        self._all_compartments.update(
            {sim_data.molecule_ids.full_chromosome[:-3]: ["c"]}
        )

        # Proteins are assumed to localize to a single compartment. If there is
        # experimental evidence for the protein existing in the cytosol, the
        # protein is assigned to the cytosol. If there are multiple
        # experimentally determined localizations other than the cytosol, the
        # first compartment given in the list is used. If an experimentally
        # determined localization doesn't exist, the first compartment given in
        # the list of computationally determined compartments is used. If no
        # compartment information is given, the protein is assumed to localize
        # to the cytosol.
        for protein in raw_data.proteins:
            exp_compartment = protein["experimental_compartment"]
            comp_compartment = protein["computational_compartment"]

            if (
                "CCO-CYTOSOL" in exp_compartment
                or len(exp_compartment) + len(comp_compartment) == 0
            ):
                compartment = "CCO-CYTOSOL"
            elif len(exp_compartment) > 0:
                compartment = exp_compartment[0]
            else:
                compartment = comp_compartment[0]

            self._all_compartments.update(
                {protein["id"]: [compartment_ids_to_abbreviations[compartment]]}
            )

        # Modified proteins localize to the single compartment specified in raw
        # data for each protein
        self._all_compartments.update(
            {
                protein["id"]: [protein["compartment"]]
                for protein in raw_data.modified_proteins
            }
        )

        # Metabolites localize to the cytosol plus any other compartment that
        # is specified in the list of metabolic reactions, or the periplasm if
        # the metabolite is a 2CS ligand
        metabolite_compartments = {met["id"]: ["c"] for met in raw_data.metabolites}

        # Loop through list of metabolic reactions and add missing compartments
        # for each metabolite
        for rxn in raw_data.metabolic_reactions:
            for met in rxn["stoichiometry"].keys():
                met_id, compartment, _ = re.split(r"[\[\]]", met)
                compartment_tag = compartment_ids_to_abbreviations[compartment]

                if (
                    met_id in metabolite_compartments
                    and compartment_tag not in metabolite_compartments[met_id]
                ):
                    metabolite_compartments[met_id].append(compartment_tag)

        # Add periplasm as valid compartment for 2CS ligands
        two_component_system_ligands = [
            system["molecules"]["LIGAND"] for system in raw_data.two_component_systems
        ]

        for met in two_component_system_ligands:
            if "p" not in metabolite_compartments[met]:
                metabolite_compartments[met].append("p")

        self._all_compartments.update(metabolite_compartments)

        # Polymerized subunits can localize to all compartments being modeled
        self._all_compartments.update(
            {
                subunit_id[:-3]: all_compartments
                for subunit_id in sim_data.molecule_groups.polymerized_subunits
            }
        )

        # Sort compartment abbreviations based on complexation priority: when
        # a molecule in a compartment with a higher complexation priority
        # forms a complex with a molecule in a compartment with low priority,
        # the complex is assigned to the higher-priority compartment.
        complexation_priorities = np.array(
            [comp["complexation_priority"] for comp in raw_data.compartments]
        )
        all_compartments_sorted = [
            all_compartments[i] for i in np.argsort(complexation_priorities)
        ]

        # Protein complexes are localized based on the compartments of their
        # subunits
        self._all_compartments.update(
            self._build_protein_complex_compartments(raw_data, all_compartments_sorted)
        )

    def _build_protein_complex_compartments(self, raw_data, all_compartments_sorted):
        """
        Builds dictionary of compartment tags for protein complexes keyed with
        the molecule IDs. Each complex is assigned to the compartment that
        contains a subunit of the complex and has the highest complexation
        priority. Compartment tags of subunits that are also protein complexes
        are determined recursively. Compartments of metabolite subunits are not
        considered since metabolites are assigned to all compartments that are
        being modeled.
        """
        protein_complex_compartments = {}
        metabolite_ids = {met["id"] for met in raw_data.metabolites}

        # Build mapping from complex ID to its subunit IDs
        complex_id_to_subunit_ids = {}

        for rxn in itertools.chain(
            raw_data.complexation_reactions, raw_data.equilibrium_reactions
        ):
            # Get the ID of the complex and the stoichiometry of subunits
            complex_id = None
            subunit_ids = []

            for mol_id, coeff in rxn["stoichiometry"].items():
                # Replace miscRNA subunit IDs with TU IDs
                if mol_id in self._miscrna_id_to_singleton_tu_id:
                    mol_id = self.get_singleton_tu_id(mol_id)

                if coeff is None:
                    coeff = -1

                if coeff == 1:
                    complex_id = mol_id
                elif coeff < 0:
                    subunit_ids.append(mol_id)

            complex_id_to_subunit_ids[complex_id] = subunit_ids

        def get_compartment(mol_id):
            """
            Recursive function that returns a compartment tag given a molecule ID.
            """
            # Look for molecule ID in existing dictionaries, and return
            # compartment tag if found
            if mol_id in self._all_compartments:
                return self._all_compartments[mol_id]
            elif mol_id in protein_complex_compartments:
                return protein_complex_compartments[mol_id]

            # If molecule ID does not exist, return the subunit compartment
            # with the highest priority
            else:
                all_subunit_compartments = []
                for subunit_id in complex_id_to_subunit_ids[mol_id]:
                    # Skip metabolites (can exist in all compartments)
                    if subunit_id in metabolite_ids:
                        continue
                    else:
                        all_subunit_compartments.extend(get_compartment(subunit_id))

                if len(all_subunit_compartments) == 0:
                    raise InvalidProteinComplexError(
                        "Complex %s is composed of only metabolites." % (mol_id,)
                    )

                # Return compartment with highest complexation priority
                for loc in all_compartments_sorted:
                    if loc in all_subunit_compartments:
                        return [loc]

        # Get compartments of each protein complex
        for complex_id in complex_id_to_subunit_ids.keys():
            protein_complex_compartments.update(
                {complex_id: get_compartment(complex_id)}
            )

        return protein_complex_compartments

    def _build_genomic_coordinates(self, raw_data):
        """
        Builds a dictionary of genomic coordinates of DNA sites. Keys are the
        IDs of the sites, and the values are tuples of the left-end coordinate
        and the right-end coordinate of the site. Sites whose types are included
        in IGNORED_DNA_SITE_TYPES are ignored.
        """
        self._all_genomic_coordinates = {
            site["id"]: (site["left_end_pos"], site["right_end_pos"])
            for site in raw_data.dna_sites
            if site["type"] not in IGNORED_DNA_SITE_TYPES
        }
