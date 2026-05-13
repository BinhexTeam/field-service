# Copyright 2026 Binhex
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.exceptions import UserError

from odoo.addons.base.tests.common import BaseCommon


class TestSubcontractReassignment(BaseCommon):
    """Test worker reassignment protection when PO exists."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor_partner = cls.env["res.partner"].create(
            {
                "name": "Test Vendor",
                "supplier_rank": 1,
            }
        )
        cls.location = cls.env["fsm.location"].create(
            {
                "name": "Test Location",
                "partner_id": cls.env["res.partner"].create({"name": "Client"}).id,
                "owner_id": cls.env["res.partner"]
                .create({"name": "Location Owner"})
                .id,
            }
        )
        cls.service_product = cls.env["product.product"].create(
            {
                "name": "Subcontracted Service",
                "type": "service",
                "purchase_method": "receive",
                "uom_id": cls.env.ref("uom.product_uom_hour").id,
            }
        )
        cls.order_type = cls.env["fsm.order.type"].create(
            {
                "name": "Test Type",
            }
        )
        cls.template = cls.env["fsm.template"].create(
            {
                "name": "Test Template",
                "type_id": cls.order_type.id,
                "subcontract_product_id": cls.service_product.id,
            }
        )
        cls.closed_stage = cls.env["fsm.stage"].create(
            {
                "name": "Closed Reassignment Test",
                "stage_type": "order",
                "is_closed": True,
            }
        )
        cls.subcontractor = cls.env["fsm.person"].create(
            {
                "name": "External Worker",
                "partner_id": cls.vendor_partner.id,
                "supplier_rank": 1,
                "is_subcontractor": True,
            }
        )
        cls.internal_worker = cls.env["fsm.person"].create(
            {
                "name": "Internal Worker",
                "partner_id": cls.env["res.partner"].create({"name": "Internal"}).id,
            }
        )
        cls.replacement_worker = cls.env["fsm.person"].create(
            {
                "name": "Replacement Worker",
                "partner_id": cls.env["res.partner"].create({"name": "Replacement"}).id,
            }
        )

    def _create_fso(self):
        return self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )

    def _create_fso_with_po(self):
        fso = self._create_fso()
        fso._create_subcontract_po()
        self.assertTrue(fso.purchase_order_ids)
        return fso

    def _create_posted_vendor_bill(self, purchase_order):
        expense_account = self.env["account.account"].search(
            [("account_type", "=", "expense")],
            limit=1,
        )
        self.assertTrue(expense_account)
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": purchase_order.partner_id.id,
                "invoice_date": fields.Date.context_today(purchase_order),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": purchase_order.order_line.name,
                            "product_id": purchase_order.order_line.product_id.id,
                            "quantity": 1.0,
                            "price_unit": 100.0,
                            "purchase_line_id": purchase_order.order_line.id,
                            "account_id": expense_account.id,
                        },
                    )
                ],
            }
        )
        bill.action_post()
        purchase_order.invalidate_recordset(["invoice_ids"])
        return bill

    def test_reassignment_is_blocked_without_wizard(self):
        """Changing worker on FSO with PO should be blocked."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )
        fso._create_subcontract_po()
        self.assertTrue(fso.purchase_order_ids)

        with self.assertRaises(UserError):
            fso.write({"person_id": self.internal_worker.id})
        self.assertEqual(fso.person_id, self.subcontractor)

    def test_reassignment_action_opens_wizard(self):
        """Reassignment action should open the confirmation wizard."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )
        fso._create_subcontract_po()

        result = fso.action_open_reassign_confirm()
        self.assertEqual(result["res_model"], "fsm.order.reassign.confirm")
        self.assertEqual(
            result["context"]["default_fsm_order_id"],
            fso.id,
        )

    def test_reassign_worker_button_requires_created_po_and_open_stage(self):
        """Reassign Worker button should require a linked PO and open stage."""
        fso = self._create_fso()
        self.assertFalse(fso.reassign_worker)

        fso._create_subcontract_po()
        self.assertTrue(fso.reassign_worker)

        fso._cancel_active_subcontract_purchase_orders()
        self.assertTrue(fso.purchase_order_ids)
        self.assertFalse(fso._get_active_subcontract_purchase_orders())
        self.assertTrue(fso.reassign_worker)

        fso.stage_id = self.closed_stage
        self.assertFalse(fso.reassign_worker)

    def test_reassign_worker_button_ignores_current_worker_subcontractor_flag(self):
        """Reassign Worker button should depend on linked PO and open stage."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.internal_worker.id,
            }
        )
        wizard.action_confirm()

        self.assertFalse(fso.person_id.is_subcontractor)
        self.assertEqual(purchase_order.state, "cancel")
        self.assertFalse(fso._get_active_subcontract_purchase_orders())
        self.assertTrue(fso.reassign_worker)
        result = fso.action_open_reassign_confirm()
        self.assertEqual(result["res_model"], "fsm.order.reassign.confirm")

        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.replacement_worker.id,
            }
        )
        wizard.action_confirm()
        self.assertEqual(fso.person_id, self.replacement_worker)

    def test_reassign_worker_button_stays_hidden_on_closed_stage_for_internal_worker(
        self,
    ):
        """Closed stage should hide reassignment even for internal current worker."""
        fso = self._create_fso_with_po()
        fso.with_context(skip_reassign_check=True).person_id = self.internal_worker
        fso._cancel_active_subcontract_purchase_orders()
        fso.stage_id = self.closed_stage

        self.assertFalse(fso.person_id.is_subcontractor)
        self.assertFalse(fso._get_active_subcontract_purchase_orders())
        self.assertFalse(fso.reassign_worker)

    def test_reassignment_action_without_po_is_blocked(self):
        """Reassignment action should require a subcontract PO."""
        fso = self._create_fso()

        with self.assertRaises(UserError):
            fso.action_open_reassign_confirm()

    def test_reassignment_action_with_cancelled_po_opens_wizard(self):
        """Reassignment action should allow an FSO with only cancelled POs."""
        fso = self._create_fso_with_po()
        fso._cancel_active_subcontract_purchase_orders()

        result = fso.action_open_reassign_confirm()

        self.assertFalse(fso._get_active_subcontract_purchase_orders())
        self.assertEqual(result["res_model"], "fsm.order.reassign.confirm")
        self.assertEqual(
            result["context"]["default_fsm_order_id"],
            fso.id,
        )

    def test_reassignment_action_on_closed_stage_is_blocked(self):
        """Reassignment action should be blocked when FSO stage is closed."""
        fso = self._create_fso_with_po()
        fso.stage_id = self.closed_stage

        with self.assertRaises(UserError):
            fso.action_open_reassign_confirm()

    def test_reassign_wizard_warning_without_purchase_orders(self):
        """Reassign wizard should stay quiet when there are no active POs."""
        fso = self._create_fso()
        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.internal_worker.id,
            }
        )

        self.assertFalse(wizard.purchase_order_ids)
        self.assertFalse(wizard.warning_message)

    def test_reassign_wizard_warning_with_active_purchase_order(self):
        """Reassign wizard should list the POs that will be cancelled."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.internal_worker.id,
            }
        )

        self.assertEqual(wizard.purchase_order_ids, purchase_order)
        self.assertIn(purchase_order.name, wizard.warning_message)

    def test_reassign_wizard_warning_with_posted_bill(self):
        """Posted vendor bills should block automatic PO cancellation."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        self._create_posted_vendor_bill(purchase_order)
        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.internal_worker.id,
            }
        )

        self.assertIn("posted vendor bills", wizard.warning_message)
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertEqual(fso.person_id, self.subcontractor)

    def test_reassign_wizard_on_closed_stage_is_blocked(self):
        """Wizard should not reassign when the FSO stage is closed."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        fso.stage_id = self.closed_stage
        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.internal_worker.id,
            }
        )

        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertEqual(purchase_order.state, "draft")
        self.assertEqual(fso.person_id, self.subcontractor)

    def test_reassignment_with_skip_context(self):
        """Reassignment should succeed when skip_reassign_check is set."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )
        fso._create_subcontract_po()
        fso.with_context(skip_reassign_check=True).write(
            {"person_id": self.internal_worker.id}
        )
        self.assertEqual(fso.person_id, self.internal_worker)

    def test_reassignment_without_wizard_is_blocked_with_cancelled_po(self):
        """Changing worker directly should be blocked when a PO was created."""
        fso = self._create_fso_with_po()
        fso._cancel_active_subcontract_purchase_orders()

        with self.assertRaises(UserError):
            fso.write({"person_id": self.internal_worker.id})

    def test_wizard_cancels_po(self):
        """Wizard action_confirm should cancel PO and reassign."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )
        fso._create_subcontract_po()
        po = fso.purchase_order_ids

        wizard = self.env["fsm.order.reassign.confirm"].create(
            {
                "fsm_order_id": fso.id,
                "new_person_id": self.internal_worker.id,
            }
        )
        self.assertEqual(wizard.purchase_order_ids, po)
        wizard.action_confirm()
        self.assertEqual(po.state, "cancel")
        self.assertEqual(fso.person_id, self.internal_worker)
