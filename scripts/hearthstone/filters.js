var filters = {

    filterStates : {},

    filterStatesDefault : {

        "mode" : {
            "standard" : true,
            "wild" : false
        },

        "cardClass" : {
            "NEUTRAL" : true,
            "WARRIOR" : true,
            "ROGUE" : true,
            "MAGE" : true,
            "PALADIN" : true,
            "PRIEST" : true,
            "DRUID" : true,
            "WARLOCK" : true,
            "SHAMAN" : true,
            "HUNTER" : true
        },

        "type" : {
            "MINION" : true,
            "WEAPON" : true,
            "SPELL" : true
        },

        "rarity" : {
            "FREE" : true,
            "COMMON" : true,
            "RARE" : true,
            "EPIC" : true,
            "LEGENDARY" : true
        },

        "race" : {
            "" : true,
            "BEAST" : true,
            "ELEMENTAL" : true,
            "MECHANICAL" : true,
            "PIRATE" : true,
            "DEMON" : true,
            "DRAGON" : true,
            "MURLOC" : true,
            "TOTEM" : true
        },

        "cost" : {
            "0" : true,
            "1" : true,
            "2" : true,
            "3" : true,
            "4" : true,
            "5" : true,
            "6" : true,
            "7plus" : true
        }

    },

    mechanicFilter : "null",

    filteredList : [],

    resetFilter : function(){
        filters.filterStates = jQuery.extend(true, {}, filters.filterStatesDefault);
    },

    handle : function(div){
        var params = div.split("-");

        var upperCase = ["cardClass", "type", "rarity", "mechanics", "race"];

        if (upperCase.indexOf(params[1]) != -1){
            params[2] = params[2].toUpperCase();
        }

        filters.filterStates[params[1]][params[2]] = !filters.filterStates[params[1]][params[2]];

        if (filters.filterStates[params[1]][params[2]] == true){
            $("#" + div).removeClass("not-selected");
            $("#" + div).addClass("selected");
        } else {
            $("#" + div).removeClass("selected");
            $("#" + div).addClass("not-selected");
        }

        filters.filter(params[1]);
    },

    selectHandle : function(value){
        filters.mechanicFilter = value
        filters.filter();
    },

    filter : function(){
        filters.filteredList = [];
        sort.resetSort();

        $.each(cardData, function(card, data){
            data.filtered = true;
            // mode filter
            if (filters.filterStates.mode[data.mode] != true){
                data.filtered = false ;
            }
            //class filters
            else if (filters.filterStates.cardClass[data.cardClass] != true ){
                data.filtered = false;
            }
            // type filter
            else if (filters.filterStates.type[data.type] != true ){
                data.filtered = false;
            }
            // rarity filter
            else if (filters.filterStates.rarity[data.rarity] != true){
                data.filtered = false;
            }
            // tribe filter
            else if (filters.filterStates.race[data.race] != true){
                data.filtered = false;
            }
            // cost filter
            else if (filters.filterStates.cost[data.costStr] != true) {
                data.filtered = false;
            }
            // mechanic filter
            if (filters.mechanicFilter != "null"){
                if (data.mechanics.indexOf(filters.mechanicFilter) == -1){
                    data.filtered = false;
                }
            }


            if (data.filtered == true){
                filters.filteredList.push(data);
            }
        })

        sort.sortData("name", "asc");
        sort.changeHeader("display-name");
        filters.writeTable();
    },

    writeTable : function(){


      $("#display-cardstotal").text(filters.filteredList.length)

      $("#display-table-data").empty();

      $.each(filters.filteredList, function(i, data){

              var divider = "</div><div>";

              var toAdd = "<div onclick='filters.displayCard(\"" + data.url
               + "\")' class='card-row " + dict.class[data.cardClass] + "'><div>";


              toAdd += data.name + divider + dict.classText[data.cardClass] + divider + dict.type[data.type]
                        + divider + dict.rarity[data.rarity] + divider + dict.set[data.set] +
                        divider + data.cost + "</div></div>";
              $("#display-table-data").append(toAdd);

      });




    },


    displayCard : function(path){
        var url = "https://cdn.rawgit.com/schmich/hearthstone-card-images/" + path
        var toAdd =  "<img src='" + url + "' />"
        $("#card").html(toAdd)

    },

    selectAllStatus : false,

    selectAll: function(status){
        $.each(filters.filterStates, function(filter, states){
            $.each(states, function(key, val){

                states[key] = filters.selectAllStatus;

                if (filters.selectAllStatus == false){
                    $("#filters-" + filter + "-" + key.toLowerCase()).removeClass("selected");
                    $("#filters-" + filter + "-" + key.toLowerCase()).addClass("not-selected");
                    $('#filter-mechanic-select').prop('selectedIndex',0);
                    filters.mechanicFilter = "null";
                } else {
                    $("#filters-" + filter + "-" + key.toLowerCase()).removeClass("not-selected");
                    $("#filters-" + filter + "-" + key.toLowerCase()).addClass("selected");
                    $('#filter-mechanic-select').prop('selectedIndex',0);
                    filters.mechanicFilter = "null";
                }
            })
        })

        if (filters.selectAllStatus == true){
            $("#filters-select-button").text("Deselect All");
        } else {
            $("#filters-select-button").text("Select All");
        }

        filters.selectAllStatus = !filters.selectAllStatus;
        filters.filter();
    }

}
