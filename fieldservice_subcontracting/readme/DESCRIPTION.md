This module integrates Field Service with Purchasing to automate the
subcontracting workflow.

It allows users to:

1. Mark Field Service workers as subcontractors.
2. Configure a service product on Field Service order templates.
3. Create draft Purchase Orders for subcontracted orders.
4. Update delivered quantities from Field Service timesheets.
5. Reassign workers on orders with linked subcontract Purchase Orders.

The module uses `fieldservice_stage_server_action` to trigger automation on
stage transitions.
