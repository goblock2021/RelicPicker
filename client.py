"""
Smithbox gRPC client — read-only data loading.
Connects to Smithbox's Soapstone server at localhost:22720.
"""

import sys
import os
import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "proto"))

import internal_pb2
import soapstone_pb2
import soapstone_pb2_grpc


class SmithboxClient:
    """Thin gRPC wrapper for reading param data from Smithbox."""

    def __init__(self, host: str = "127.0.0.1", port: int = 22720):
        self.address = f"{host}:{port}"
        self.channel = grpc.insecure_channel(self.address)
        try:
            grpc.channel_ready_future(self.channel).result(timeout=5)
        except grpc.FutureTimeoutError:
            raise ConnectionError(
                f"无法连接到 Smithbox ({self.address})，"
                f"请确认 Smithbox 已启动且 gRPC 服务已开启。"
            )
        self.stub = soapstone_pb2_grpc.SoapstoneStub(self.channel)

    def close(self):
        self.channel.close()

    # ── Param inspection ──────────────────────────────────────────────

    def list_params(self) -> list[str]:
        """Get all param names in the loaded project."""
        resp = self.stub.ListParams(internal_pb2.ListParamsRequest())
        return list(resp.param_names)

    def describe_param(self, param_name: str) -> dict:
        """Get field definitions for a param."""
        resp = self.stub.DescribeParam(
            internal_pb2.DescribeParamRequest(param_name=param_name)
        )
        return {
            "param_name": resp.param_name,
            "param_type": resp.param_type,
            "row_count": resp.row_count,
            "fields": [
                {
                    "display_name": f.display_name,
                    "internal_name": f.internal_name,
                    "def_type": f.def_type,
                    "bit_size": f.bit_size,
                    "array_length": f.array_length,
                    "description": f.description,
                }
                for f in resp.fields
            ],
        }

    def list_param_rows(self, param_name: str, vanilla: bool = False) -> list[dict]:
        """List all rows (ID + Name + Index)."""
        resp = self.stub.ListParamRows(
            internal_pb2.ListParamRowsRequest(
                param_name=param_name, vanilla=vanilla)
        )
        return [
            {"id": r.id, "name": r.name, "index": r.index}
            for r in resp.rows
        ]

    def get_param_row(self, param_name: str, row_index: int,
                      vanilla: bool = False) -> dict:
        """Get all field values for a row by index."""
        resp = self.stub.GetParamRow(
            internal_pb2.GetParamRowRequest(
                param_name=param_name, row_index=row_index, vanilla=vanilla)
        )
        return {
            "param_name": resp.param_name,
            "row_id": resp.row_id,
            "row_index": resp.row_index,
            "row_name": resp.row_name,
            "fields": {v.internal_name: v.value for v in resp.values},
        }

    def get_param_rows(self, param_name: str, row_id: int,
                       vanilla: bool = False) -> list[dict]:
        """Get ALL rows sharing a given ID."""
        resp = self.stub.GetParamRows(
            internal_pb2.GetParamRowsRequest(
                param_name=param_name, row_id=row_id, vanilla=vanilla)
        )
        return [
            {
                "param_name": resp.param_name,
                "row_id": r.row_id,
                "row_index": r.row_index,
                "row_name": r.row_name,
                "fields": {v.internal_name: v.value for v in r.values},
            }
            for r in resp.rows
        ]

    # ── Mass edit (for apply) ─────────────────────────────────────────

    def execute_mass_edit(self, script: str) -> dict:
        """Execute a Mass Edit script."""
        resp = self.stub.ExecuteMassEdit(
            internal_pb2.ExecuteMassEditRequest(script=script)
        )
        return {"success": resp.success, "result": resp.result}

    def set_param_cell(self, param_name: str, row_index: int,
                        field_name: str, value) -> dict:
        """Set a single cell value by row index."""
        resp = self.stub.SetParamCell(
            internal_pb2.SetParamCellRequest(
                param_name=param_name,
                row_index=row_index,
                field_name=field_name,
                value=str(value),
            )
        )
        return {"success": resp.success, "message": resp.message}

    def reload_params(self, *param_names: str) -> dict:
        """Hot-reload params to game memory."""
        req = internal_pb2.ReloadParamsRequest()
        if param_names:
            req.param_names.extend(param_names)
        resp = self.stub.ReloadParams(req)
        return {
            "reloaded": list(resp.reloaded_params),
            "failed": list(resp.failed_params),
        }

    def reload_param(self, param_name: str) -> dict:
        return self.reload_params(param_name)
