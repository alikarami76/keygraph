package models

type Order struct {
	ID         uint   `gorm:"column:id;primaryKey"`
	CustomerID uint   `gorm:"column:customer_id"`
	State      string `gorm:"column:status"`
}

func (Order) TableName() string { return "orders" }
