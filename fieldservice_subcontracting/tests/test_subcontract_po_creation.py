# Copyright 2026 Binhex
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.base.tests.common import BaseCommon


@tagged("post_install", "-at_install")
class TestSubcontractPOCreation(BaseCommon):
    """Test automatic Purchase Order creation for subcontracted FSOs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Vendor partner
        cls.vendor_partner = cls.env["res.partner"].create(
            {
                "name": "Test Vendor Co.",
                "supplier_rank": 1,
            }
        )
        # Non-vendor partner
        cls.non_vendor_partner = cls.env["res.partner"].create(
            {
                "name": "Test Non-Vendor",
                "supplier_rank": 0,
            }
        )
        # FSM location
        cls.location = cls.env["fsm.location"].create(
            {
                "name": "Test Location",
                "partner_id": cls.env["res.partner"].create({"name": "Test Client"}).id,
                "owner_id": cls.env["res.partner"]
                .create({"name": "Test Location Owner"})
                .id,
            }
        )
        # Service product for subcontracting
        cls.service_product = cls.env["product.product"].create(
            {
                "name": "Subcontracted Service",
                "type": "service",
                "purchase_method": "receive",
                "uom_id": cls.env.ref("uom.product_uom_hour").id,
            }
        )
        # FSM Order Type with subcontracting template
        cls.order_type = cls.env["fsm.order.type"].create(
            {
                "name": "Test Service Type",
            }
        )
        cls.template = cls.env["fsm.template"].create(
            {
                "name": "Test Service Template",
                "type_id": cls.order_type.id,
                "subcontract_product_id": cls.service_product.id,
            }
        )
        # FSM Order Type without subcontracting product in template
        cls.order_type_no_product = cls.env["fsm.order.type"].create(
            {
                "name": "Test Type No Product",
            }
        )
        cls.template_no_product = cls.env["fsm.template"].create(
            {
                "name": "Test Template No Product",
                "type_id": cls.order_type_no_product.id,
            }
        )
        # Subcontractor worker
        cls.subcontractor = cls.env["fsm.person"].create(
            {
                "name": "External Vendor Worker",
                "partner_id": cls.vendor_partner.id,
                "supplier_rank": 1,
                "is_subcontractor": True,
            }
        )
        # Internal worker
        cls.internal_worker = cls.env["fsm.person"].create(
            {
                "name": "Internal Worker",
                "partner_id": cls.env["res.partner"].create({"name": "Internal"}).id,
                "is_subcontractor": False,
            }
        )
        # Project with analytic account
        cls.analytic_account = cls.env["account.analytic.account"].create(
            {
                "name": "Test Analytic",
                "plan_id": cls.env.ref("analytic.analytic_plan_projects").id,
            }
        )
        cls.project = cls.env["project.project"].create(
            {
                "name": "Test Project",
                "account_id": cls.analytic_account.id,
            }
        )

    def _create_fso(self, worker=None, order_type=None, template=None, project=None):
        """Helper to create an FSO."""
        vals = {
            "location_id": self.location.id,
        }
        if worker:
            vals["person_id"] = worker.id
        if order_type:
            vals["type"] = order_type.id
        if template:
            vals["template_id"] = template.id
        if project:
            vals["project_id"] = project.id
        return self.env["fsm.order"].create(vals)

    def test_subcontractor_constraint_vendor(self):
        """Marking a non-vendor partner as subcontractor should fail."""
        with self.assertRaises(ValidationError):
            self.env["fsm.person"].create(
                {
                    "name": "Bad Worker",
                    "partner_id": self.non_vendor_partner.id,
                    "is_subcontractor": True,
                }
            )

    def test_create_po_for_subcontractor(self):
        """PO should be created when _create_subcontract_po is called."""
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
            project=self.project,
        )
        self.assertFalse(fso.purchase_order_ids)
        fso._create_subcontract_po()
        self.assertTrue(fso.purchase_order_ids)
        po = fso.purchase_order_ids
        self.assertEqual(po.partner_id, self.vendor_partner)
        self.assertEqual(po.fsm_order_id, fso)
        self.assertEqual(po.state, "draft")
        self.assertEqual(po.origin, fso.name)
        self.assertEqual(len(po.order_line), 1)
        line = po.order_line[0]
        self.assertEqual(line.product_id, self.service_product)
        self.assertEqual(line.product_qty, 1.0)
        # Check analytic distribution
        expected_dist = {str(self.analytic_account.id): 100.0}
        self.assertEqual(line.analytic_distribution, expected_dist)

    def test_no_po_for_internal_worker(self):
        """No PO should be created for internal (non-subcontractor) workers."""
        fso = self._create_fso(
            worker=self.internal_worker,
            order_type=self.order_type,
            template=self.template,
        )
        fso._create_subcontract_po()
        self.assertFalse(fso.purchase_order_ids)

    def test_no_duplicate_po(self):
        """Calling _create_subcontract_po twice should not create a second PO."""
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
        )
        fso._create_subcontract_po()
        po1 = fso.purchase_order_ids
        fso._create_subcontract_po()
        self.assertEqual(fso.purchase_order_ids, po1)
        self.assertEqual(len(fso.purchase_order_ids), 1)

    def test_new_po_after_previous_po_cancelled(self):
        """A new PO can be created after the previous PO was cancelled."""
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
        )
        fso._create_subcontract_po()
        po1 = fso.purchase_order_ids
        po1.button_cancel()

        fso._create_subcontract_po()
        self.assertEqual(len(fso.purchase_order_ids), 2)
        self.assertEqual(len(fso._get_active_subcontract_purchase_orders()), 1)

    def test_no_po_without_product(self):
        """No PO if the order type has no subcontracting product."""
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type_no_product,
            template=self.template_no_product,
        )
        fso._create_subcontract_po()
        self.assertFalse(fso.purchase_order_ids)

    def test_no_po_without_assigned_worker(self):
        """No PO if the FSO has no assigned worker."""
        fso = self._create_fso(
            order_type=self.order_type,
            template=self.template,
        )

        fso._create_subcontract_po()

        self.assertFalse(fso.purchase_order_ids)
        self.assertTrue(
            any(
                "It does not have an assigned worker." in message.body
                for message in fso.message_ids
            )
        )

    def test_no_po_when_subcontractor_partner_is_not_supplier(self):
        """No PO if subcontractor data becomes inconsistent."""
        self.vendor_partner.supplier_rank = 0
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
        )

        fso._create_subcontract_po()

        self.assertFalse(fso.purchase_order_ids)
        self.assertTrue(
            any(
                "not associated as a supplier" in message.body
                for message in fso.message_ids
            )
        )

    def test_smart_button_count(self):
        """Purchase order count should reflect PO existence."""
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
        )
        self.assertEqual(fso.purchase_order_count, 0)
        fso._create_subcontract_po()
        self.assertEqual(fso.purchase_order_count, 1)

    def test_action_view_purchase_order(self):
        """Purchase Order smart button should open linked POs."""
        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
        )
        fso._create_subcontract_po()
        purchase_order = fso.purchase_order_ids

        action = fso.action_view_purchase_order()

        self.assertEqual(action["res_model"], "purchase.order")
        self.assertEqual(action["res_id"], purchase_order.id)
        self.assertEqual(action["view_mode"], "form")

        purchase_order.button_cancel()
        fso._create_subcontract_po()
        action = fso.action_view_purchase_order()

        self.assertEqual(action["view_mode"], "list,form")
        self.assertEqual(action["domain"], [("id", "in", fso.purchase_order_ids.ids)])
        self.assertNotIn("res_id", action)

    def test_action_view_fsm_order_from_purchase_order(self):
        """Purchase Order smart button should open the linked FSO."""
        unlinked_purchase_order = self.env["purchase.order"].create(
            {
                "partner_id": self.vendor_partner.id,
            }
        )
        self.assertEqual(unlinked_purchase_order.fsm_order_count, 0)

        fso = self._create_fso(
            worker=self.subcontractor,
            order_type=self.order_type,
            template=self.template,
        )
        fso._create_subcontract_po()
        purchase_order = fso.purchase_order_ids

        action = purchase_order.action_view_fsm_order()

        self.assertEqual(purchase_order.fsm_order_count, 1)
        self.assertEqual(action["res_model"], "fsm.order")
        self.assertEqual(action["res_id"], fso.id)
        self.assertEqual(action["view_mode"], "form")
