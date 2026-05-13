# Copyright 2026 Binhex
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.base.tests.common import BaseCommon


@tagged("post_install", "-at_install")
class TestSubcontractPOClose(BaseCommon):
    """Test PO delivered quantity update when FSO is closed."""

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
        cls.subcontractor = cls.env["fsm.person"].create(
            {
                "name": "External Worker",
                "partner_id": cls.vendor_partner.id,
                "supplier_rank": 1,
                "is_subcontractor": True,
            }
        )
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Test Employee",
            }
        )
        cls.project = cls.env["project.project"].create(
            {
                "name": "Test Project",
            }
        )

    def test_update_po_qty_on_close(self):
        """Closing the FSO should update PO line qty_received."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
                "project_id": self.project.id,
            }
        )
        # Create PO
        fso._create_subcontract_po()
        po = fso.purchase_order_ids
        self.assertTrue(po)

        # Simulate timesheet entries
        self.env["account.analytic.line"].create(
            {
                "name": "Work done - day 1",
                "project_id": self.project.id,
                "fsm_order_id": fso.id,
                "employee_id": self.employee.id,
                "unit_amount": 4.0,
            }
        )
        self.env["account.analytic.line"].create(
            {
                "name": "Work done - day 2",
                "project_id": self.project.id,
                "fsm_order_id": fso.id,
                "employee_id": self.employee.id,
                "unit_amount": 3.5,
            }
        )

        # Update delivered qty
        fso._update_subcontract_po_qty()

        po_line = po.order_line[0]
        self.assertAlmostEqual(po_line.product_qty, 7.5)
        self.assertAlmostEqual(po_line.qty_received, 7.5)

    def test_no_update_without_subcontract_product(self):
        """PO quantity should not update when FSO has no subcontract product."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
                "project_id": self.project.id,
            }
        )
        fso._create_subcontract_po()
        po_line = fso.purchase_order_ids.order_line
        self.env["account.analytic.line"].create(
            {
                "name": "Work done",
                "project_id": self.project.id,
                "fsm_order_id": fso.id,
                "employee_id": self.employee.id,
                "unit_amount": 2.0,
            }
        )
        fso.write(
            {
                "type": self.order_type_no_product.id,
                "template_id": self.template_no_product.id,
            }
        )

        fso._update_subcontract_po_qty()

        self.assertAlmostEqual(po_line.product_qty, 1.0)
        self.assertAlmostEqual(po_line.qty_received, 0.0)

    def test_no_update_without_po(self):
        """Should not fail if FSO has no PO linked."""
        fso = self.env["fsm.order"].create(
            {
                "location_id": self.location.id,
                "person_id": self.subcontractor.id,
                "type": self.order_type.id,
                "template_id": self.template.id,
            }
        )
        # Should not raise
        fso._update_subcontract_po_qty()
