<?php

#[ORM\Table(name: 'legacy_orders')]
class LegacyOrder
{
    #[ORM\Column]
    private int $status;
}
