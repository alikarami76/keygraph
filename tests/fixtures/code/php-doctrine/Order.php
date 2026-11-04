<?php

namespace App\Entity;

use Doctrine\ORM\Mapping as ORM;

#[ORM\Entity]
#[ORM\Table(name: 'orders')]
class Order
{
    #[ORM\Id]
    #[ORM\Column(type: 'integer')]
    private int $id;

    #[ORM\Column(name: 'customer_id', type: 'integer')]
    private int $buyer;

    #[ORM\Column]
    private string $status;

    public function getStatus(): string
    {
        return $this->status;
    }
}
