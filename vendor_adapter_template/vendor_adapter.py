"""Copy and implement this package; it intentionally cannot submit or claim a device."""
from qtest_bench.adapter import Adapter
from qtest_bench.contracts import QTestError


class VendorAdapter(Adapter):
    def __init__(self, config):
        self.config = config
        # Initialize a pinned vendor SDK here. Read credentials locally; set network timeouts.

    def describe(self):
        # Return the actual device identity and only capabilities verified against that device.
        raise QTestError("Template: implement the actual device descriptor")

    def submit(self, workload, request_id):
        # Validate native feasibility, compile, preserve request_id, and return provider job_id.
        # Never retry an ambiguous submission without resolving request_id at the provider.
        raise QTestError("Template: implement SDK submission and mapping")

    def status(self, job_id):
        # Translate to queued / running / completed / failed / cancelled.
        raise QTestError("Template: implement SDK status")

    def result(self, job_id):
        # Return raw counts with canonical logical ordering and complete attempted-shot count.
        raise QTestError("Template: implement raw measurement conversion")

    def cancel(self, job_id):
        # Request cancellation; returning here does not confirm that execution has stopped.
        raise QTestError("Template: implement best-effort cancellation")
