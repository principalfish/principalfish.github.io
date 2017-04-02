var filters = {

    filterStates : {},

    filterStatesDefault : {
        "class" : {
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
        }

    },

    filteredList : [],

    resetFilter : function(){
        filters.filterStates = jQuery.extend(true, {}, filters.filterStatesDefault);
    },

    handle : function(div){
        params = div.split("-");

        params[2] = params[2].toUpperCase();

        filters.filterStates[params[1]][params[2]] = !filters.filterStates[params[1]][params[2]];

        if (filters.filterStates[params[1]][params[2]] == true){
            $("#" + div).removeClass("not-selected");
            $("#" + div).addClass("selected");
        } else {
            $("#" + div).removeClass("selected");
            $("#" + div).addClass("not-selected");
        }

        filters.filter();
    },

    filter : function(){
        filters.filteredList = [];
        sort.resetSort();


        $.each(cardData, function(card, data){

            data.filtered = true;
            //class filters
            if (filters.filterStates.class[data.cardClass] != true ){
                data.filtered = false;
            }

            else if (filters.filterStates.type[data.type] != true ){
                data.filtered = false;
            }


            if (data.filtered == true){
                filters.filteredList.push(data);
            }


        })

        sort.sortData("name", "asc");
        filters.writeTable();
    },

    writeTable : function(){

      $("#display-cardstotal").text(filters.filteredList.length)

      $("#display-table-data").empty();

      $.each(filters.filteredList, function(i, data){
              var divider = "</div><div>";
              var toAdd = "<div class='" + dict.class[data.cardClass] + "'><div>";

              toAdd += data.name + divider + dict.classText[data.cardClass] + divider + dict.type[data.type]
                        + divider + dict.rarity[data.rarity] + divider + dict.set[data.set] +
                        divider + data.cost + "</div></div>";
              $("#display-table-data").append(toAdd);

      })
    }
}
