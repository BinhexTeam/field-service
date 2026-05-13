# Copyright 2026 Binhex
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.exceptions import UserError

from odoo.addons.base.tests.common import BaseCommon


class TestSubcontractCancel(BaseCommon):
    """Test FSO cancellation confirmation when subcontract POs exist."""

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
        cls.subcontractor = cls.env["fsm.person"].create(
            {
                "name": "External Worker",
                "partner_id": cls.vendor_partner.id,
                "supplier_rank": 1,
                "is_subcontractor": True,
            }
        )

    def _create_fso_with_po(self):
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

    def test_action_cancel_opens_wizard_when_active_po_exists(self):
        """Cancel action should ask what to do with active POs."""
        fso = self._create_fso_with_po()

        result = fso.action_cancel()

        self.assertEqual(result["res_model"], "fsm.order.cancel.confirm")
        self.assertEqual(result["context"]["default_fsm_order_id"], fso.id)
        self.assertNotEqual(
            fso.stage_id,
            self.env.ref("fieldservice.fsm_stage_cancelled"),
        )

    def test_action_cancel_blocks_multiple_orders_with_active_pos(self):
        """Multiple FSOs with active POs should be cancelled one at a time."""
        fso_1 = self._create_fso_with_po()
        fso_2 = self._create_fso_with_po()

        with self.assertRaises(UserError):
            (fso_1 | fso_2).action_cancel()

    def test_cancel_wizard_warning_without_purchase_orders(self):
        """Cancel wizard should stay quiet when there are no active POs."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )
        wizard = self.env["fsm.order.cancel.confirm"].create(
            {
                "fsm_order_id": fso.id,
            }
        )

        self.assertFalse(wizard.purchase_order_ids)
        self.assertFalse(wizard.warning_message)

    def test_cancel_wizard_warning_with_active_purchase_order(self):
        """Cancel wizard should list active POs in its warning."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        wizard = self.env["fsm.order.cancel.confirm"].create(
            {
                "fsm_order_id": fso.id,
            }
        )

        self.assertEqual(wizard.purchase_order_ids, purchase_order)
        self.assertIn(purchase_order.name, wizard.warning_message)

    def test_cancel_wizard_blocks_po_cancellation_with_posted_bill(self):
        """Posted vendor bills should block automatic PO cancellation."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        bill = self._create_posted_vendor_bill(purchase_order)
        wizard = self.env["fsm.order.cancel.confirm"].create(
            {
                "fsm_order_id": fso.id,
            }
        )

        self.assertEqual(fso._get_posted_subcontract_vendor_bills(), bill)
        self.assertIn("posted vendor bills", wizard.warning_message)
        with self.assertRaises(UserError):
            wizard.action_cancel_fsm_and_purchase_orders()
        self.assertNotEqual(purchase_order.state, "cancel")

    def test_cancel_fso_only_keeps_purchase_order_open(self):
        """Wizard can cancel only the FSO."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        wizard = self.env["fsm.order.cancel.confirm"].create(
            {
                "fsm_order_id": fso.id,
            }
        )

        wizard.action_cancel_fsm_only()

        self.assertEqual(fso.stage_id, self.env.ref("fieldservice.fsm_stage_cancelled"))
        self.assertNotEqual(purchase_order.state, "cancel")

    def test_cancel_fso_and_purchase_orders(self):
        """Wizard can cancel the FSO and related active POs."""
        fso = self._create_fso_with_po()
        purchase_order = fso.purchase_order_ids
        wizard = self.env["fsm.order.cancel.confirm"].create(
            {
                "fsm_order_id": fso.id,
            }
        )

        wizard.action_cancel_fsm_and_purchase_orders()

        self.assertEqual(fso.stage_id, self.env.ref("fieldservice.fsm_stage_cancelled"))
        self.assertEqual(purchase_order.state, "cancel")
