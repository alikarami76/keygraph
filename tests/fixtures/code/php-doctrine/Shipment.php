<?php

namespace App\Entity;

use Doctrine\ORM\Mapping as ORM;

/**
 * @ORM\Entity
 * @ORM\Table(name="shipments")
 */
class Shipment
{
    /**
     * @ORM\Column(name="tracking_no", type="string")
     */
    private $trackingNumber;

    /**
     * @ORM\Column(type="string")
     */
    private $carrier;
}
