#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
#  Copyright 2019 The FATE Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from arch.api.utils import log_utils
from federatedml.feature import feature_selection
from federatedml.feature.hetero_feature_selection.base_feature_selection import BaseHeteroFeatureSelection
from federatedml.protobuf.generated import feature_selection_param_pb2
from federatedml.util import consts

LOGGER = log_utils.getLogger()


class HeteroFeatureSelectionHost(BaseHeteroFeatureSelection):
    def __init__(self):
        super(HeteroFeatureSelectionHost, self).__init__()

        self.static_obj = None
        self.iv_attrs = None
        self.fit_iv = False
        self.results = []
        self.header = []
        self.flowid = ''
        self.party_name = consts.HOST
        self.fed_filter_count = 0

    def fit(self, data_instances):
        LOGGER.info("Start Hetero Selection Fit and transform.")

        self._abnormal_detection(data_instances)
        self._init_cols(data_instances)
        LOGGER.debug("filter methods: {}".format(self.filter_methods))
        for method in self.filter_methods:
            self.filter_one_method(data_instances, method)

        new_data = self._transfer_data(data_instances)
        LOGGER.info("Finish Hetero Selection Fit and transform.")

        return new_data

    def transform(self, data_instances):
        self._abnormal_detection(data_instances)
        # self._init_cols(data_instances)
        self._transform_init_cols(data_instances)

        LOGGER.info("[Result][FeatureSelection][Host]In transform, Self left cols are: {}".format(
            self.filter_result.get_left_cols()
        ))
        new_data = self._transfer_data(data_instances)
        return new_data

    def filter_one_method(self, data_instances, method):

        if method == consts.IV_VALUE_THRES:
            LOGGER.debug("In host party, sending select_cols")
            self._send_select_cols(consts.IV_VALUE_THRES)
            self._received_result_cols(filter_name=consts.IV_VALUE_THRES)
            LOGGER.info(
                "[Result][FeatureSelection][Host]Finish iv value threshold filter. Current left cols are: {}".format(
                    self.filter_result.get_left_cols()))

        if method == consts.IV_PERCENTILE:
            self._send_select_cols(consts.IV_PERCENTILE)
            self._received_result_cols(filter_name=consts.IV_PERCENTILE)
            LOGGER.info("[Result][FeatureSelection][Host]Finish iv percentile filter. Current left cols are: {}".format(
                self.filter_result.get_left_cols()))

        if method == consts.COEFFICIENT_OF_VARIATION_VALUE_THRES:
            variance_coe_param = self.model_param.variance_coe_param
            coe_filter = feature_selection.CoeffOfVarValueFilter(variance_coe_param,
                                                                 self.filter_result.this_to_select_cols_index,
                                                                 self.static_obj)
            new_left_cols = coe_filter.fit(data_instances)
            # self._renew_final_left_cols(new_left_cols)
            self.filter_result.add_left_col_index(new_left_cols)

            self.static_obj = coe_filter.statics_obj
            self.variance_coe_meta = coe_filter.get_meta_obj()
            self.results.append(coe_filter.get_param_obj())
            LOGGER.debug(
                "[Result][FeatureSelection][Host]Finish coeffiecient_of_variation value threshold filter."
                " Current left cols are: {}".format(
                    self.filter_result.get_left_cols()))

        if method == consts.UNIQUE_VALUE:
            unique_param = self.model_param.unique_param
            unique_filter = feature_selection.UniqueValueFilter(unique_param,
                                                                self.filter_result.this_to_select_cols_index,
                                                                self.static_obj)
            new_left_cols = unique_filter.fit(data_instances)
            # self._renew_final_left_cols(new_left_cols)
            self.filter_result.add_left_col_index(new_left_cols)

            self.static_obj = unique_filter.statics_obj
            self.unique_meta = unique_filter.get_meta_obj()
            self.results.append(unique_filter.get_param_obj())
            # self._renew_left_col_names()
            LOGGER.info("[Result][FeatureSelection][Host]Finish unique value filter. Current left cols are: {}".format(
                self.filter_result.get_left_cols()))

        if method == consts.OUTLIER_COLS:
            outlier_param = self.model_param.outlier_param
            outlier_filter = feature_selection.OutlierFilter(outlier_param,
                                                             self.filter_result.this_to_select_cols_index)
            new_left_cols = outlier_filter.fit(data_instances)
            # self._renew_final_left_cols(new_left_cols)
            self.filter_result.add_left_col_index(new_left_cols)

            self.outlier_meta = outlier_filter.get_meta_obj()
            self.results.append(outlier_filter.get_param_obj())
            # self._renew_left_col_names()
            LOGGER.info("[Result][FeatureSelection][Host]Finish outlier cols filter. Current left cols are: {}".format(
                self.filter_result.get_left_cols()))

    def _received_result_cols(self, filter_name):

        left_cols_index = self.transfer_variable.result_left_cols.get(idx=0,
                                                                      suffix=(filter_name,))
        original_cols = self.filter_result.this_to_select_cols
        self.filter_result.add_left_col_index(left_cols_index)
        LOGGER.info("Received left columns from guest, received left_cols: {}".format(left_cols_index))

        LOGGER.debug(f"left_cols: {self.filter_result.get_left_cols()}")
        left_col_obj = feature_selection_param_pb2.LeftCols(original_cols=original_cols,
                                                            left_cols=self.filter_result.get_left_cols())

        result_obj = feature_selection_param_pb2.FeatureSelectionFilterParam(feature_values={},
                                                                             left_cols=left_col_obj,
                                                                             filter_name=filter_name)
        self.results.append(result_obj)
        # LOGGER.info("Received Left cols are {}".format(self.left_cols))

    def _send_select_cols(self, filter_name):
        self.transfer_variable.host_select_cols.remote(self.filter_result.this_to_select_cols_index,
                                                       role=consts.GUEST,
                                                       idx=0,
                                                       suffix=(filter_name,))

        LOGGER.info("Sent select cols to guest")
