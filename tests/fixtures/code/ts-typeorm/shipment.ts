import { Column, Entity, PrimaryGeneratedColumn } from "typeorm";

@Entity("shipments")
export class Shipment {
  @PrimaryGeneratedColumn()
  id: number;

  @Column({ name: "order_id" })
  orderId: number;

  @Column()
  carrier: string;

  @Column({ name: "tracking_no", nullable: true })
  trackingNo?: string;
}

@Entity({ name: "customers" })
export class Customer {
  @Column()
  email: string;
}
