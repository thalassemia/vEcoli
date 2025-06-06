"""
SimulationData for Ecoli

Raw data processed into forms convenient for whole-cell modeling

"""

from __future__ import annotations

import collections

import numpy as np

# Data classes
from reconstruction.ecoli.dataclasses.getter_functions import (
    GetterFunctions,
    EXCLUDED_RNA_TYPES,
)
from reconstruction.ecoli.dataclasses.molecule_groups import MoleculeGroups
from reconstruction.ecoli.dataclasses.molecule_ids import MoleculeIds
from reconstruction.ecoli.dataclasses.constants import Constants
from reconstruction.ecoli.dataclasses.common_names import CommonNames
from reconstruction.ecoli.dataclasses.state.internal_state import InternalState
from reconstruction.ecoli.dataclasses.state.external_state import ExternalState
from reconstruction.ecoli.dataclasses.process.process import Process
from reconstruction.ecoli.dataclasses.growth_rate_dependent_parameters import (
    Mass,
    GrowthRateParameters,
)
from reconstruction.ecoli.dataclasses.relation import Relation
from reconstruction.ecoli.dataclasses.adjustments import Adjustments
from wholecell.utils.fitting import normalize


VERBOSE = False


class SimulationDataEcoli(object):
    """SimulationDataEcoli"""

    def __init__(self):
        # Doubling time (used in fitting)
        self.doubling_time = None

    def initialize(self, raw_data, basal_expression_condition="M9 Glucose minus AAs"):
        self.operons_on = raw_data.operons_on
        self.stable_rrna = raw_data.stable_rrna

        self._add_condition_data(raw_data)
        self.condition = "basal"
        self.doubling_time = self.condition_to_doubling_time[self.condition]

        # TODO: Check that media condition is valid
        self.basal_expression_condition = basal_expression_condition

        self._add_molecular_weight_keys(raw_data)
        self._add_compartment_keys(raw_data)
        self._add_base_codes(raw_data)

        # General helper functions (have no dependencies)
        self.common_names = CommonNames(raw_data)
        self.constants = Constants(raw_data)
        self.adjustments = Adjustments(raw_data)

        # Reference helper function for molecule IDs (can depend on preceding
        # helper functions)
        self.molecule_ids = MoleculeIds(raw_data, self)

        # Reference helper function for molecule groups (can depend on preceding
        # helper functions)
        self.molecule_groups = MoleculeGroups(raw_data, self)

        # Getter functions (can depend on helper functions and reference classes)
        self.getter = GetterFunctions(raw_data, self)

        # Growth rate dependent parameters are set first
        self.growth_rate_parameters = GrowthRateParameters(raw_data, self)
        self.mass = Mass(raw_data, self)

        # Data classes (can depend on helper and getter functions)
        # Data classes cannot depend on each other
        self.external_state = ExternalState(raw_data, self)
        self.process = Process(raw_data, self)
        self.internal_state = InternalState(raw_data, self)

        # Relations between data classes (can depend on data classes)
        # Relations cannot depend on each other
        self.relation = Relation(raw_data, self)

        self.translation_supply_rate = {}
        self.pPromoterBound = {}

    def _add_molecular_weight_keys(self, raw_data):
        self.submass_name_to_index = {
            mw_key["submass_name"]: mw_key["index"]
            for mw_key in raw_data.molecular_weight_keys
        }

    def _add_compartment_keys(self, raw_data):
        self.compartment_abbrev_to_index = {
            compartment["abbrev"]: i
            for i, compartment in enumerate(raw_data.compartments)
        }
        self.compartment_id_to_index = {
            compartment["id"]: i for i, compartment in enumerate(raw_data.compartments)
        }
        self.compartment_abbrev_to_id = {}
        for compartment in raw_data.compartments:
            self.compartment_abbrev_to_id[compartment["abbrev"]] = compartment["id"]

    def _add_base_codes(self, raw_data):
        self.amino_acid_code_to_id_ordered = collections.OrderedDict(
            tuple((row["code"], row["id"]) for row in raw_data.base_codes.amino_acids)
        )

        self.ntp_code_to_id_ordered = collections.OrderedDict(
            tuple((row["code"], row["id"]) for row in raw_data.base_codes.ntp)
        )

        self.nmp_code_to_id_ordered = collections.OrderedDict(
            tuple((row["code"], row["id"]) for row in raw_data.base_codes.nmp)
        )

        self.dntp_code_to_id_ordered = collections.OrderedDict(
            tuple((row["code"], row["id"]) for row in raw_data.base_codes.dntp)
        )

    def _add_condition_data(self, raw_data):
        abbrToActiveId = {
            x["TF"]: x["activeId"].split(", ")
            for x in raw_data.transcription_factors
            if len(x["activeId"]) > 0
        }
        gene_id_to_rna_id = {gene["id"]: gene["rna_ids"][0] for gene in raw_data.genes}
        gene_symbol_to_rna_id = {
            gene["symbol"]: gene["rna_ids"][0] for gene in raw_data.genes
        }
        gene_symbol_to_rna_id.update(
            {
                x["name"]: gene_id_to_rna_id[x["geneId"]]
                for x in raw_data.translation_efficiency
                if x["geneId"] != "#N/A"
            }
        )

        rna_ids_with_coordinates = {
            gene["rna_ids"][0]
            for gene in raw_data.genes
            if gene["left_end_pos"] is not None and gene["right_end_pos"] is not None
        }
        rna_id_to_rna_type = {rna["id"]: rna["type"] for rna in raw_data.rnas}

        self.tf_to_fold_change = {}
        self.tf_to_direction = {}

        for fc_file in ["fold_changes", "fold_changes_nca"]:
            gene_not_found = set()
            tf_not_found = set()
            gene_location_not_specified = set()
            gene_excluded = set()

            for row in getattr(raw_data, fc_file):
                FC = row["log2 FC mean"]

                # Skip fold changes that do not agree with curation
                if row["Regulation_direct"] != "" and row["Regulation_direct"] > 2:
                    continue

                # Skip positive autoregulation
                if row["TF"] == row["Target"] and FC > 0:
                    continue

                try:
                    tf = abbrToActiveId[row["TF"]][0]
                except KeyError:
                    tf_not_found.add(row["TF"])
                    continue

                try:
                    target = gene_symbol_to_rna_id[row["Target"]]
                except KeyError:
                    gene_not_found.add(row["Target"])
                    continue

                if target not in rna_ids_with_coordinates:
                    gene_location_not_specified.add(row["Target"])
                    continue

                if rna_id_to_rna_type[target] in EXCLUDED_RNA_TYPES:
                    gene_excluded.add(row["Target"])
                    continue

                if tf not in self.tf_to_fold_change:
                    self.tf_to_fold_change[tf] = {}
                    self.tf_to_direction[tf] = {}

                self.tf_to_direction[tf][target] = np.sign(FC)
                self.tf_to_fold_change[tf][target] = 2**FC

            if VERBOSE:
                if gene_not_found:
                    print(
                        f"The following target genes listed in {fc_file}.tsv"
                        " have no corresponding entry in genes.tsv:"
                    )
                    for item in gene_not_found:
                        print(item)

                if tf_not_found:
                    print(
                        "The following transcription factors listed in"
                        f" {fc_file}.tsv have no corresponding active entry in"
                        " transcription_factors.tsv:"
                    )
                    for tf in tf_not_found:
                        print(tf)

                if gene_location_not_specified:
                    print(
                        f"The following target genes listed in {fc_file}.tsv"
                        " have no chromosomal location specified in"
                        " genes.tsv:"
                    )
                    for item in gene_location_not_specified:
                        print(item)

                if gene_excluded:
                    print(
                        f"The following target genes listed in {fc_file}.tsv"
                        " have been excluded from the model:"
                    )
                    for item in gene_excluded:
                        print(item)

        self.tf_to_active_inactive_conditions = {}
        for row in raw_data.condition.tf_condition:
            tf = row["active TF"]

            if tf not in self.tf_to_fold_change:
                continue

            activeGenotype = row["active genotype perturbations"]
            activeNutrients = row["active nutrients"]
            inactiveGenotype = row["inactive genotype perturbations"]
            inactiveNutrients = row["inactive nutrients"]

            if tf not in self.tf_to_active_inactive_conditions:
                self.tf_to_active_inactive_conditions[tf] = {}
            else:
                print("Warning: overwriting TF fold change conditions for %s" % tf)

            self.tf_to_active_inactive_conditions[tf][
                "active genotype perturbations"
            ] = activeGenotype
            self.tf_to_active_inactive_conditions[tf]["active nutrients"] = (
                activeNutrients
            )
            self.tf_to_active_inactive_conditions[tf][
                "inactive genotype perturbations"
            ] = inactiveGenotype
            self.tf_to_active_inactive_conditions[tf]["inactive nutrients"] = (
                inactiveNutrients
            )

        # Populate combined conditions data from condition_defs
        self.conditions = {}
        self.condition_to_doubling_time = {}
        self.condition_active_tfs = {}
        self.condition_inactive_tfs = {}
        self.ordered_conditions = []  # order for variant to run
        for row in raw_data.condition.condition_defs:
            condition = row["condition"]
            self.ordered_conditions.append(condition)
            self.conditions[condition] = {}
            self.conditions[condition]["nutrients"] = row["nutrients"]
            self.conditions[condition]["perturbations"] = row["genotype perturbations"]
            self.condition_to_doubling_time[condition] = row["doubling time"]
            self.condition_active_tfs[condition] = row["active TFs"]
            self.condition_inactive_tfs[condition] = row["inactive TFs"]

        # Populate nutrientToDoubling for each set of combined conditions
        self.nutrient_to_doubling_time = {}
        for condition in self.condition_to_doubling_time:
            if len(self.conditions[condition]["perturbations"]) > 0:
                continue
            nutrientLabel = self.conditions[condition]["nutrients"]
            if (
                nutrientLabel in self.nutrient_to_doubling_time
                and self.condition_to_doubling_time[condition]
                != self.nutrient_to_doubling_time[nutrientLabel]
            ):
                raise Exception(
                    "Multiple doubling times correspond to the same media conditions"
                )
            self.nutrient_to_doubling_time[nutrientLabel] = (
                self.condition_to_doubling_time[condition]
            )

        # Populate conditions and conditionToDboulingTime for active and inactive TF conditions
        basal_dt = self.condition_to_doubling_time["basal"]
        for tf in sorted(self.tf_to_active_inactive_conditions):
            for status in ["active", "inactive"]:
                condition = "{}__{}".format(tf, status)
                nutrients = self.tf_to_active_inactive_conditions[tf][
                    "{} nutrients".format(status)
                ]
                self.conditions[condition] = {}
                self.conditions[condition]["nutrients"] = nutrients
                self.conditions[condition]["perturbations"] = (
                    self.tf_to_active_inactive_conditions[tf][
                        "{} genotype perturbations".format(status)
                    ]
                )
                self.condition_to_doubling_time[condition] = (
                    self.nutrient_to_doubling_time.get(nutrients, basal_dt)
                )

    def calculate_ppgpp_expression(self, condition: str):
        """
        Calculates the expected expression of RNA based on ppGpp regulation
        in a given condition and the expected transcription factor effects in
        that condition.

        Relies on other values that are calculated in the fitting process so
        should only be called after the parca has been run.

        Args:
                condition: label for the desired condition to calculate the average
                        expression for (eg. 'basal', 'with_aa', etc)
        """

        ppgpp = self.growth_rate_parameters.get_ppGpp_conc(
            self.condition_to_doubling_time[condition]
        )
        delta_prob = self.process.transcription_regulation.get_delta_prob_matrix(
            ppgpp=True
        )
        p_promoter_bound = np.array(
            [
                self.pPromoterBound[condition][tf]
                for tf in self.process.transcription_regulation.tf_ids
            ]
        )
        delta = delta_prob @ p_promoter_bound
        prob, factor = self.process.transcription.synth_prob_from_ppgpp(
            ppgpp, self.process.replication.get_average_copy_number
        )
        rna_expression = prob * (1 + delta) / factor

        # For cases with no basal ppGpp expression, assume the delta prob is the
        # same as without ppGpp control
        mask = prob == 0
        rna_expression[mask] = delta[mask] / factor[mask]

        rna_expression[rna_expression < 0] = 0
        return normalize(rna_expression)

    def adjust_final_expression(self, gene_indices, factors):
        transcription = self.process.transcription
        transcription_regulation = self.process.transcription_regulation

        for gene_index, factor in zip(gene_indices, factors):
            recruitment_mask = np.array(
                [i == gene_index for i in transcription_regulation.delta_prob["deltaI"]]
            )
            for synth_prob in transcription.rna_synth_prob.values():
                synth_prob[gene_index] *= factor
            for exp in transcription.rna_expression.values():
                exp[gene_index] *= factor
            transcription.exp_free[gene_index] *= factor
            transcription.exp_ppgpp[gene_index] *= factor
            transcription.attenuation_basal_prob_adjustments[
                transcription.attenuated_rna_indices == gene_index
            ] *= factor
            transcription_regulation.basal_prob[gene_index] *= factor
            transcription_regulation.delta_prob["deltaV"][recruitment_mask] *= factor

        # Renormalize parameters
        for synth_prob in transcription.rna_synth_prob.values():
            synth_prob /= synth_prob.sum()
        for exp in transcription.rna_expression.values():
            exp /= exp.sum()
        transcription.exp_free /= transcription.exp_free.sum()
        transcription.exp_ppgpp /= transcription.exp_ppgpp.sum()

    def adjust_new_gene_final_expression(self, gene_indices, factors):
        """
        Adjusting the final expression values of new genes must be handled
        separately because the baseline new gene expression values need to be
        set to small non-zero values using data loaded from
        flat/new_gene_data/new_gene_baseline_expression_parameters.tsv,
        as new genes are knocked out by default.

        Args:
            gene_indices: Indices of new genes to adjust
            factors: Multiplicative factor to adjust by
        """

        transcription = self.process.transcription
        transcription_regulation = self.process.transcription_regulation

        new_gene_rna_synth_prob_baseline = transcription.new_gene_expression_baselines[
            "new_gene_rna_synth_prob_baseline"
        ]
        new_gene_rna_expression_baseline = transcription.new_gene_expression_baselines[
            "new_gene_rna_expression_baseline"
        ]
        new_gene_exp_free_baseline = transcription.new_gene_expression_baselines[
            "new_gene_exp_free_baseline"
        ]
        new_gene_exp_ppgpp_baseline = transcription.new_gene_expression_baselines[
            "new_gene_exp_ppgpp_baseline"
        ]
        new_gene_reg_basal_prob_baseline = transcription.new_gene_expression_baselines[
            "new_gene_reg_basal_prob_baseline"
        ]

        for gene_index, factor in zip(gene_indices, factors):
            recruitment_mask = np.array(
                [i == gene_index for i in transcription_regulation.delta_prob["deltaI"]]
            )
            for synth_prob in transcription.rna_synth_prob.values():
                synth_prob[gene_index] = new_gene_rna_synth_prob_baseline * factor

            for exp in transcription.rna_expression.values():
                exp[gene_index] = new_gene_rna_expression_baseline * factor

            transcription.exp_free[gene_index] = new_gene_exp_free_baseline * factor
            transcription.exp_ppgpp[gene_index] = new_gene_exp_ppgpp_baseline * factor
            transcription_regulation.basal_prob[gene_index] = (
                new_gene_reg_basal_prob_baseline * factor
            )

            # For the forseeable future, these will not be needed in the new
            # gene implementation. For now, encode the assumption that these
            # will be empty numpy arrays.
            assert (
                (
                    transcription.attenuation_basal_prob_adjustments[
                        transcription.attenuated_rna_indices == gene_index
                    ]
                ).size
                == 0
            ), (
                "Attenuation basal probability adjustment for new genes is"
                " not currently implemented in the model."
            )
            assert (
                (transcription_regulation.delta_prob["deltaV"][recruitment_mask]).size
                == 0
            ), (
                "Transcriptional regulation of new genes is not currently"
                " implemented in the model."
            )

        # Renormalize parameters
        for synth_prob in transcription.rna_synth_prob.values():
            synth_prob /= synth_prob.sum()
        for exp in transcription.rna_expression.values():
            exp /= exp.sum()
        transcription.exp_free /= transcription.exp_free.sum()
        transcription.exp_ppgpp /= transcription.exp_ppgpp.sum()
