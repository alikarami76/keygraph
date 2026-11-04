<?php

function itemsOf($db, $orderId)
{
    $sql = <<<SQL
        SELECT qty, unit_price
        FROM order_items
        WHERE order_id = {$orderId}
        SQL;
    return $db->query($sql);
}
